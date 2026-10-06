import copy
import gzip
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from inspect_lakehouse import (
    ValidationError, fit_bounds, inspect, inspect_geojson, inspect_pmtiles,
    inspect_cog, require_fields, require_geometry,
    main,
)
from map_layers import vector_layer, raster_layer, source_fragment, text_filter, number_filter


def point(properties=None, coordinates=None):
    return {"type": "Feature", "properties": properties or {"name": "A", "STATE": "06", "count": 1},
            "geometry": {"type": "Point", "coordinates": coordinates or [-121, 38]}}


class LakehouseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def geojson(self, features, **metadata):
        path = self.root / "source.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", **metadata, "features": features}),
                        encoding="utf-8-sig")
        return path

    def test_bom_full_count_filtered_extent_and_text_codes(self):
        path = self.geojson([point(), point({"name": "B", "STATE": "53", "count": 2}, [-123, 48])])
        before = path.read_bytes()
        report = inspect_geojson(path, [{"field": "STATE", "op": "eq", "value": "06"}])
        self.assertEqual(report["featureCount"], 2)
        self.assertEqual(report["selectedCount"], 1)
        self.assertEqual(report["selectedBounds"], [-121, 38, -121, 38])
        self.assertEqual(text_filter(report, "STATE", ["06"])["value"], ["06"])
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(inspect_geojson(path, [{"field": "STATE", "op": "eq", "value": 6}])["errors"])

    def test_feature_limit_is_whole_file_not_selection(self):
        path = self.geojson([point(), point({"name": "B"})])
        with patch("inspect_lakehouse.FEATURE_LIMIT", 1):
            report = inspect_geojson(path, [{"field": "name", "op": "eq", "value": "A"}])
        self.assertEqual(report["selectedCount"], 1)
        self.assertIn("PMTiles", report["errors"][0])
        with self.assertRaises(ValidationError):
            source_fragment(report, "s", "n", "d", "Files/source.geojson")

    def test_large_geojson_is_not_rejected_by_a_local_byte_limit(self):
        path = self.geojson([point()])
        with path.open("ab") as output:
            output.write(b" " * (21 * 1024 * 1024))
        report = inspect_geojson(path)
        self.assertGreater(report["bytes"], 20 * 1024 * 1024)
        self.assertEqual(report["featureCount"], 1)
        self.assertFalse(report["errors"])
        self.assertEqual(source_fragment(report, "s", "n", "d", "Files/source.geojson")["type"], "geojson")

    def test_exact_100000_feature_boundary(self):
        feature = point({"n": 1})
        report = inspect_geojson(self.geojson([feature] * 100000))
        self.assertEqual(report["featureCount"], 100000)
        self.assertFalse(report["errors"])
        report = inspect_geojson(self.geojson([feature] * 100001))
        self.assertEqual(report["featureCount"], 100001)
        self.assertIn("100,001", report["errors"][0])

    def test_properties_missing_null_and_wrong_types(self):
        report = inspect_geojson(self.geojson([point(), point({"name": None, "count": "2"}), point({"other": True})]))
        self.assertEqual(report["fields"]["name"]["missing"], 1)
        self.assertEqual(report["fields"]["name"]["types"]["null"], 1)
        with self.assertRaisesRegex(ValidationError, "FieldDoesNotExist"):
            require_fields(report, ["FieldDoesNotExist"])
        with self.assertRaisesRegex(ValidationError, "must be numeric"):
            require_fields(report, ["count"], ["count"])

    def test_malformed_filters_and_truncated_json_report_explicit_errors(self):
        path = self.geojson([point()])
        for filters in ({}, [{"field": "missing"}], [{"field": "missing", "op": "gt", "value": "1"}]):
            with self.subTest(filters=filters), self.assertRaises(ValidationError):
                inspect_geojson(path, filters)
        path.write_text('{"type":"FeatureCollection","features":[', encoding="utf-8")
        errors = io.StringIO()
        with patch.object(sys, "argv", ["inspect_lakehouse.py", str(path)]), redirect_stderr(errors):
            self.assertEqual(main(), 2)
        self.assertTrue(json.loads(errors.getvalue())["errors"])

    def test_invalid_structure_geometry_coordinates_crs(self):
        bad = [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [200, 90]}},
            {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [2, 2]]]}},
            {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[0, 0]]}},
            {"type": "Feature", "properties": {}, "geometry": {"type": "GeometryCollection", "geometries": []}},
            {"type": "Feature", "geometry": None},
        ]
        for feature in bad:
            with self.subTest(feature=feature), self.assertRaises(ValidationError):
                inspect_geojson(self.geojson([feature]))
        with self.assertRaisesRegex(ValidationError, "CRS"):
            inspect_geojson(self.geojson([point()], crs={"type": "name", "properties": {"name": "EPSG:3857"}}))
        with self.assertRaisesRegex(ValidationError, "CRS"):
            inspect_geojson(self.geojson([point()], crs={"type": "link", "properties": {"href": "unknown"}}))

    def test_polygon_not_point_and_empty_geometry_not_fabricated(self):
        polygon = point()
        polygon["geometry"] = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]}
        empty = point()
        empty["geometry"] = {"type": "MultiPolygon", "coordinates": []}
        report = inspect_geojson(self.geojson([polygon, empty]))
        self.assertEqual(report["emptyGeometryCount"], 1)
        with self.assertRaisesRegex(ValidationError, "MultiPolygon"):
            require_geometry(report, "point")

    def test_projection_errors_are_reported_without_changing_the_source(self):
        cases = [
            (self.geojson([point()], crs={"type": "name", "properties": {"name": "EPSG:3857"}}),
             "Unsupported GeoJSON CRS"),
        ]
        projected = self.root / "projected.geojson"
        projected.write_text(json.dumps({
            "type": "FeatureCollection", "features": [point(coordinates=[1000000, 2000000])],
        }), encoding="utf-8")
        cases.append((projected, "WGS84 longitude/latitude"))
        cases.append((self.cog(crs="EPSG:4326"), "Unsupported COG projection EPSG:4326"))
        for path, expected in cases:
            with self.subTest(path=path):
                before = path.read_bytes()
                output, errors = io.StringIO(), io.StringIO()
                with patch.object(sys, "argv", ["inspect_lakehouse.py", str(path)]), \
                        redirect_stdout(output), redirect_stderr(errors):
                    self.assertEqual(main(), 2)
                report = json.loads(errors.getvalue() or output.getvalue())
                self.assertTrue(any(expected in error for error in report["errors"]))
                self.assertEqual(path.read_bytes(), before)
                self.assertNotIn("reproject", " ".join(report["errors"]).lower())

    def test_shared_fields_labels_colors_size_and_filters(self):
        report = inspect_geojson(self.geojson([point(), point({"name": "B", "STATE": "53", "count": 4})]))
        baseline = copy.deepcopy(report)
        layer = vector_layer(report, "s", "stations", "point", label="name", color_by="STATE",
                             size_by="count", tooltips=["count"], filters=[number_filter(report, "count", 1, 4)])
        self.assertEqual(layer["options"]["dataLabelKeys"], ["name"])
        self.assertEqual(layer["options"]["bubbleOptions"]["sizeProperty"], "count")
        self.assertEqual(layer["options"]["bubbleOptions"]["color"][0], "match")
        self.assertIn("06", layer["options"]["bubbleOptions"]["color"])
        self.assertEqual(report, baseline)
        with self.assertRaisesRegex(ValidationError, "FieldDoesNotExist"):
            vector_layer(report, "s", "bad", "point", label="FieldDoesNotExist")
        with self.assertRaises(ValidationError):
            source_fragment(report, "s", "bad", "d", "Files/../source.geojson")

    def test_polygon_fill_and_category_colors_are_renderable(self):
        feature = point()
        feature["geometry"] = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
        report = inspect_geojson(self.geojson([feature]))
        layer = vector_layer(report, "s", "polygons", "polygon", color_by="STATE")
        self.assertNotIn("lineOptions", layer["options"])
        self.assertEqual(layer["options"]["polygonOptions"]["fillOpacity"], 0.65)
        self.assertEqual(layer["options"]["polygonOptions"]["fillColor"][0], "match")
        palette = {"06": "#FF0000", "53": "#0000FF"}
        layer = vector_layer(report, "s", "polygons", "polygon", color_by="STATE", category_colors=palette)
        self.assertEqual(layer["options"]["polygonOptions"]["customColors"], palette)
        self.assertIn("53", layer["options"]["polygonOptions"]["fillColor"])

    def test_line_style_uses_geometry_compatible_settings(self):
        feature = point()
        feature["geometry"] = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
        report = inspect_geojson(self.geojson([feature]))
        layer = vector_layer(report, "s", "lines", "line", label="name", color_by="STATE", opacity=0.5)
        self.assertNotIn("polygonOptions", layer["options"])
        self.assertEqual(layer["options"]["lineOptions"]["strokeColor"][0], "match")
        self.assertEqual(layer["options"]["lineOptions"]["strokeOpacity"], 0.5)
        self.assertEqual(layer["options"]["dataLabelKeys"], ["name"])

    def test_viewport_uses_selected_bounds_and_is_regional(self):
        wa = fit_bounds([-124.73, 45.54, -116.91, 49.01])
        york = fit_bounds([-1.11, 53.93, -1.03, 53.98])
        self.assertTrue(5 < wa["zoom"] < 7)
        self.assertTrue(11 < york["zoom"] < 14)
        self.assertTrue(-1.11 < york["center"][0] < -1.03)
        bounds = [-124.73, 45.54, -116.91, 49.01]
        minimum_zoom = wa["zoom"] + 1
        constrained = fit_bounds(bounds, minimum_zoom=minimum_zoom)
        self.assertEqual(constrained["zoom"], minimum_zoom)
        self.assertEqual(constrained["center"], wa["center"])
        self.assertEqual(fit_bounds(bounds, minimum_zoom=1), wa)
        with self.assertRaises(ValidationError):
            fit_bounds([-180, -90, 180, 90])

    def pmtiles(self, version=3, metadata=None, layers=None):
        import mapbox_vector_tile
        from pmtiles.writer import Writer
        from pmtiles.tile import Compression, TileType
        path = self.root / "source.pmtiles"
        with path.open("wb") as output:
            writer = Writer(output)
            tile = mapbox_vector_tile.encode(layers if layers is not None else [
                {"name": "points", "features": [
                    {"geometry": "POINT(10 10)", "properties": {"name": "A", "count": 2}, "id": 1}]}])
            writer.write_tile(0, gzip.compress(tile))
            writer.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP,
                             "min_lon_e7": -1800000000, "min_lat_e7": -850000000,
                             "max_lon_e7": 1800000000, "max_lat_e7": 850000000,
                             "center_lon_e7": 0, "center_lat_e7": 0, "center_zoom": 0},
                            metadata if metadata is not None else
                            {"vector_layers": [{"id": "points", "fields": {"name": "String", "count": "Number"}}]})
        if version != 3:
            data = bytearray(path.read_bytes())
            data[7] = version
            path.write_bytes(data)
        return path

    def test_polygon_overlays_keep_geometry_filters_and_shared_format_styling(self):
        def polygon(properties, west, south, east, north):
            return {"type": "Feature", "properties": properties, "geometry": {
                "type": "Polygon", "coordinates": [
                    [[west, south], [east, south], [east, north], [west, north], [west, south]]],
            }}

        path = self.geojson([
            polygon({"STATE": "06"}, -124, 32, -114, 42),
            polygon({"STATE": "53"}, -125, 46, -117, 49),
        ])
        before = path.read_bytes()
        report = inspect_geojson(path, [{"field": "STATE", "op": "eq", "value": "06"}])
        counties = vector_layer(report, "counties", "Counties", "polygon",
                                filters=[text_filter(report, "STATE", ["06"])])
        self.assertEqual(counties["filters"][0]["value"], ["06"])
        self.assertIn("polygonOptions", counties["options"])
        self.assertNotIn("lineOptions", counties["options"])
        self.assertEqual(report["selectedBounds"], [-124, 32, -114, 42])
        camera = fit_bounds(report["selectedBounds"])
        self.assertTrue(32 < camera["center"][1] < 42)
        self.assertGreater(camera["zoom"], fit_bounds(report["bounds"])["zoom"])
        self.assertEqual(path.read_bytes(), before)

        properties = {"FIRE_NAME": "Example fire", "DECADES": "2000"}
        fire = polygon(properties, -121, 37, -120, 38)
        geojson = inspect_geojson(self.geojson([fire]))
        archive = inspect_pmtiles(self.pmtiles(
            metadata={"vector_layers": [{
                "id": "fires", "fields": {"FIRE_NAME": "String", "DECADES": "String"},
            }]},
            layers=[{"name": "fires", "features": [{
                "geometry": "POLYGON((10 10, 20 10, 20 20, 10 20, 10 10))",
                "properties": properties, "id": 1,
            }]}],
        ), ["fires"])
        settings = {"label": "FIRE_NAME", "color_by": "DECADES",
                    "category_colors": {"2000": "#FF0000"}, "tooltips": ["FIRE_NAME", "DECADES"]}
        geo_layer = vector_layer(geojson, "geo", "GeoJSON fires", "polygon", **settings)
        tile_layer = vector_layer(archive, "tiles", "PMTiles fires", "polygon",
                                  source_layer="fires", **settings)
        for key in ("polygonOptions", "dataLabelKeys", "tooltipKeys"):
            self.assertEqual(geo_layer["options"][key], tile_layer["options"][key])
        for layer in (geo_layer, tile_layer):
            self.assertNotIn("lineOptions", layer["options"])
        self.assertEqual(tile_layer["sourceLayerId"], "fires")

    def test_pmtiles_internal_layer_discovery_and_fragments(self):
        report = inspect_pmtiles(self.pmtiles(), ["points"])
        self.assertEqual(report["layers"]["points"]["geometryFamilies"], ["point"])
        layer = vector_layer(report, "s", "points", "point", source_layer="points", label="name")
        self.assertEqual(layer["sourceLayerId"], layer["options"]["sourceLayer"])
        self.assertEqual(len(report["layers"]), 1)
        with self.assertRaisesRegex(ValidationError, "DoesNotExist.*points"):
            inspect_pmtiles(self.pmtiles(), ["DoesNotExist"])

    def test_pmtiles_version_bounds_and_raster_type_fail_closed(self):
        with self.assertRaisesRegex(ValidationError, "version"):
            inspect_pmtiles(self.pmtiles(2))
        path = self.pmtiles()
        data = bytearray(path.read_bytes())
        data[99] = 2
        path.write_bytes(data)
        with self.assertRaisesRegex(ValidationError, "MVT"):
            inspect_pmtiles(path)
        path = self.pmtiles()
        data = bytearray(path.read_bytes())
        data[100] = 10
        path.write_bytes(data)
        with self.assertRaisesRegex(ValidationError, "zoom"):
            inspect_pmtiles(path)
        with self.assertRaisesRegex(ValidationError, "center disagrees"):
            inspect_pmtiles(self.pmtiles(metadata={"center": "1,0,0"}))
        with self.assertRaisesRegex(ValidationError, "must be objects"):
            inspect_pmtiles(self.pmtiles(metadata={"vector_layers": ["points"]}))
        with self.assertRaisesRegex(ValidationError, "minzoom disagrees"):
            inspect_pmtiles(self.pmtiles(metadata={"minzoom": 0.5}))

    def cog(self, *, cog=True, crs="EPSG:3857", bands=3, dtype="uint8"):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.enums import ColorInterp
        path = self.root / "raster.tif"
        with rasterio.open(path, "w", driver="COG" if cog else "GTiff", width=1024, height=1024,
                           count=bands, dtype=dtype, crs=crs, transform=from_origin(0, 50, 0.01, 0.01)) as ds:
            ds.write(np.ones((bands, 1024, 1024), dtype=dtype))
            if bands == 3:
                ds.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue)
        return path

    def test_cog_structure_projection_bands_type_and_opacity(self):
        good = inspect_cog(self.cog())
        self.assertFalse(good["errors"])
        self.assertEqual(raster_layer(good, "s", "imagery", 0.75)["options"]["opacity"], 0.75)
        for kwargs in ({"cog": False}, {"crs": "EPSG:4326"}, {"bands": 1}, {"dtype": "uint16"}):
            with self.subTest(kwargs=kwargs):
                bad = inspect_cog(self.cog(**kwargs))
                self.assertTrue(bad["errors"])
                with self.assertRaises(ValidationError):
                    raster_layer(bad, "s", "bad")

    def test_format_detection_uses_content_not_suffix(self):
        path = self.pmtiles()
        renamed = path.with_suffix(".geojson")
        path.rename(renamed)
        self.assertEqual(inspect(renamed)["format"], "pmtiles")
        path = self.geojson([point()])
        renamed = path.with_suffix(".bin")
        path.rename(renamed)
        self.assertEqual(inspect(renamed)["format"], "geojson")


if __name__ == "__main__":
    unittest.main()
