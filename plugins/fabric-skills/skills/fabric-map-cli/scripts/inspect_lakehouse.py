"""Read-only, local preflight for resolved OneLake geospatial files.

Never writes a Map or changes source data. See references/authoring/lakehouse.md
for scoped discovery, authenticated downloads, and the terminal write gate.
"""

import argparse
from collections import Counter
import gzip
import json
import math
import mmap
from pathlib import Path
import sys

import ijson


class ValidationError(ValueError):
    """A source or requested binding cannot safely be used."""


FAMILIES = {
    "Point": "point", "MultiPoint": "point",
    "LineString": "line", "MultiLineString": "line",
    "Polygon": "polygon", "MultiPolygon": "polygon",
}
FEATURE_LIMIT = 100_000


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def value_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object" if isinstance(value, dict) else "array"


def merge_bounds(a, b):
    if a is None:
        return list(b) if b is not None else None
    if b is None:
        return list(a)
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def validate_bounds(bounds):
    require(isinstance(bounds, (list, tuple)) and len(bounds) == 4 and all(number(v) for v in bounds),
            f"Invalid bounds: {bounds}")
    west, south, east, north = bounds
    require(-180 <= west <= east <= 180 and -90 <= south <= north <= 90,
            f"Invalid or antimeridian-crossing bounds {bounds}; resolve an explicit camera before writing")
    return list(bounds)


def fit_bounds(bounds, width=1024, height=640, padding=64, minimum_zoom=1):
    """Fit Web Mercator bounds with padding and a caller-supplied minimum zoom."""
    west, south, east, north = validate_bounds(bounds)
    require(width > padding * 2 and height > padding * 2, "Viewport is smaller than padding")
    require(number(minimum_zoom) and 1 <= minimum_zoom <= 22, "Invalid minimum zoom")
    require(east - west <= 180, "World/antimeridian bounds need an explicit camera, not a local fit")

    def mercator_y(latitude):
        latitude = max(-85.05112878, min(85.05112878, latitude))
        return (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2

    top, bottom = mercator_y(north), mercator_y(south)
    x_span, y_span = (east - west) / 360, bottom - top
    zoom = min(
        math.log2((width - padding * 2) / (512 * max(x_span, 1e-12))),
        math.log2((height - padding * 2) / (512 * max(y_span, 1e-12))),
        22,
    )
    latitude = math.degrees(math.atan(math.sinh(math.pi * (1 - (top + bottom)))))
    return {"center": [(west + east) / 2, latitude], "zoom": max(minimum_zoom, zoom)}


def geometry_bounds(geometry):
    if geometry is None:
        return None, None
    require(isinstance(geometry, dict), "Geometry must be an object or null")
    kind = geometry.get("type")
    require(kind in FAMILIES, f"Unsupported geometry {kind!r}; expected point, line, or polygon family")
    coordinates = geometry.get("coordinates")
    if coordinates == []:
        return kind, None

    def position(point):
        require(isinstance(point, list) and len(point) in (2, 3) and all(number(v) for v in point),
                "Coordinates must be finite longitude/latitude positions (optional altitude)")
        require(-180 <= point[0] <= 180 and -90 <= point[1] <= 90,
                "GeoJSON coordinates must be WGS84 longitude/latitude")
        return [point[0], point[1], point[0], point[1]]

    def sequence(points, minimum, child):
        require(isinstance(points, list) and len(points) >= minimum, "Empty or undersized coordinate array")
        bounds = None
        for point in points:
            bounds = merge_bounds(bounds, child(point))
        return bounds

    def ring(points):
        bounds = sequence(points, 4, position)
        require(points[0] == points[-1], "Polygon rings must be closed")
        return bounds

    line = lambda points: sequence(points, 2, position)
    polygon = lambda rings: sequence(rings, 1, ring)
    readers = {
        "Point": position,
        "MultiPoint": lambda points: sequence(points, 1, position),
        "LineString": line,
        "MultiLineString": lambda lines: sequence(lines, 1, line),
        "Polygon": polygon,
        "MultiPolygon": lambda polygons: sequence(polygons, 1, polygon),
    }
    return kind, readers[kind](coordinates)


class Fields:
    def __init__(self):
        self.count = 0
        self.fields = {}

    def add(self, properties):
        self.count += 1
        for key, value in properties.items():
            stat = self.fields.setdefault(key, {"present": 0, "types": Counter(), "values": [],
                                                "valuesTruncated": False, "min": None, "max": None})
            stat["present"] += 1
            kind = value_type(value)
            stat["types"][kind] += 1
            if kind in ("string", "number", "boolean", "null") and not any(
                    value_type(v) == kind and v == value for v in stat["values"]):
                if len(stat["values"]) < 100:
                    stat["values"].append(value)
                else:
                    stat["valuesTruncated"] = True
            if kind == "number":
                stat["min"] = value if stat["min"] is None else min(stat["min"], value)
                stat["max"] = value if stat["max"] is None else max(stat["max"], value)

    def result(self):
        return {key: {**stat, "types": dict(stat["types"]), "missing": self.count - stat["present"]}
                for key, stat in sorted(self.fields.items())}


def matches(properties, filters):
    for rule in filters:
        field, op, target = rule["field"], rule["op"], rule["value"]
        if field not in properties:
            return False
        value = properties[field]
        if op == "eq":
            passed = value_type(value) == value_type(target) and value == target
        elif op == "in":
            require(isinstance(target, list), "'in' filter requires a list")
            passed = any(value_type(value) == value_type(t) and value == t for t in target)
        elif op == "gt":
            require(number(target), "'gt' filter requires a numeric threshold")
            passed = number(value) and value > target
        else:
            raise ValidationError(f"Unsupported inspection filter operator: {op}")
        if not passed:
            return False
    return True


def geojson_features(path):
    from ijson.common import ObjectBuilder

    root_type, has_features, builder, depth, crs = None, False, None, 0, None
    has_crs, crs_type = False, None
    with Path(path).open("rb") as source:
        if source.read(3) != b"\xef\xbb\xbf":
            source.seek(0)
        for prefix, event, value in ijson.parse(source, use_float=True):
            if prefix == "" and event == "start_array":
                raise ValidationError("GeoJSON root must be a FeatureCollection object")
            if prefix == "type" and event == "string":
                root_type = value
            if prefix == "crs" and event not in ("end_map", "map_key"):
                require(event == "start_map", "GeoJSON CRS must be a supported named CRS object")
                has_crs = True
            if prefix == "crs.type" and event == "string":
                crs_type = value
            if prefix == "crs.properties.name" and event == "string":
                crs = value
            if prefix == "features" and event == "start_array":
                has_features = True
            if prefix == "features.item" and builder is None:
                require(event == "start_map", "Every features entry must be a Feature object")
                builder = ObjectBuilder()
                depth = 0
            if builder is not None:
                builder.event(event, value)
                if event in ("start_map", "start_array"):
                    depth += 1
                elif event in ("end_map", "end_array"):
                    depth -= 1
                if depth == 0:
                    yield builder.value
                    builder = None
    require(root_type == "FeatureCollection" and has_features,
            "Expected GeoJSON FeatureCollection with a features array")
    require(not has_crs or (crs_type == "name" and crs in (
        "urn:ogc:def:crs:OGC:1.3:CRS84", "EPSG:4326", "urn:ogc:def:crs:EPSG::4326")),
        f"Unsupported GeoJSON CRS: type={crs_type}, name={crs}")


def inspect_geojson(path, filters=()):
    require(isinstance(filters, (list, tuple)), "Inspection filters must be an array")
    for rule in filters:
        require(isinstance(rule, dict) and {"field", "op", "value"} <= rule.keys(),
                "Each inspection filter requires field, op, and value")
        require(isinstance(rule["field"], str) and rule["field"], "Filter field must be a nonempty string")
        require(rule["op"] in ("eq", "in", "gt"), f"Unsupported inspection filter operator: {rule['op']}")
        if rule["op"] == "in":
            require(isinstance(rule["value"], list) and rule["value"], "'in' filter requires a nonempty list")
        if rule["op"] == "gt":
            require(number(rule["value"]), "'gt' filter requires a numeric threshold")
    fields, selected_fields = Fields(), Fields()
    bounds, selected_bounds = None, None
    geometries, null_geometry, empty_geometry = Counter(), 0, 0
    for index, feature in enumerate(geojson_features(path)):
        require(feature.get("type") == "Feature" and "geometry" in feature and "properties" in feature,
                f"Feature {index}: expected type, geometry, and properties")
        props = feature["properties"]
        require(props is None or isinstance(props, dict), f"Feature {index}: properties must be object or null")
        props = props or {}
        fields.add(props)
        kind, extent = geometry_bounds(feature["geometry"])
        if kind is None:
            null_geometry += 1
        else:
            geometries[kind] += 1
            if extent is None:
                empty_geometry += 1
        bounds = merge_bounds(bounds, extent)
        if matches(props, filters):
            selected_fields.add(props)
            selected_bounds = merge_bounds(selected_bounds, extent)
    result = {"format": "geojson", "bytes": Path(path).stat().st_size,
              "featureCount": fields.count, "geometryTypes": dict(geometries),
              "geometryFamilies": sorted({FAMILIES[g] for g in geometries}),
              "nullGeometryCount": null_geometry, "emptyGeometryCount": empty_geometry,
              "bounds": bounds, "fields": fields.result(),
              "selectedCount": selected_fields.count, "selectedBounds": selected_bounds,
              "selectedFields": selected_fields.result(), "filters": list(filters),
              "errors": [], "warnings": []}
    if fields.count > FEATURE_LIMIT:
        result["errors"].append(
            f"{Path(path).name}: {fields.count:,} features exceeds the documented "
            f"{FEATURE_LIMIT:,}-feature GeoJSON limit per file. Use PMTiles; "
            "a Map filter does not bypass this limit. No normal Map create is supported.")
    if null_geometry:
        result["warnings"].append(f"{null_geometry} features have null geometry and cannot be drawn")
    if empty_geometry:
        result["warnings"].append(f"{empty_geometry} features have empty coordinates and cannot be drawn")
    if not selected_bounds:
        result["errors"].append("No drawable features match; do not create a world-view or blank fallback")
    require_fields(result, [r["field"] for r in filters])
    return result


def inspect_pmtiles(path, requested_layers=(), sample_tiles=256):
    import mapbox_vector_tile
    from pmtiles.reader import Reader, all_tiles
    from pmtiles.tile import Compression, TileType

    require(sample_tiles > 0, "Tile sample budget must be positive")
    with Path(path).open("rb") as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
        require(len(data) >= 127 and data[:7] == b"PMTiles", "Not a PMTiles archive")
        require(data[7] == 3, f"Unsupported PMTiles archive version {data[7]}; this inspector supports v3")

        def get_bytes(offset, length):
            require(offset >= 0 and length >= 0 and offset + length <= len(data),
                    "PMTiles section/tile extends outside the archive")
            return data[offset:offset + length]

        reader = Reader(get_bytes)
        header = reader.header()
        for prefix in ("root", "metadata", "leaf_directory", "tile_data"):
            get_bytes(header[prefix + "_offset"], header[prefix + "_length"])
        require(header["root_offset"] >= 127 and
                header["root_offset"] + header["root_length"] <= 16384, "Invalid PMTiles root directory")
        require(header["internal_compression"] == Compression.GZIP,
                "Inspector requires gzip PMTiles directories; validate other compression with a compatible reader")
        require(header["tile_compression"] in (Compression.GZIP, Compression.NONE),
                "Inspector cannot decode this PMTiles tile compression")
        require(header["tile_type"] == TileType.MVT,
                f"Expected MVT vector PMTiles, found {header['tile_type'].name}; "
                "raster archives need the independent imagery workflow, never vector fields")
        bounds = validate_bounds([header[k] / 1e7 for k in
                                  ("min_lon_e7", "min_lat_e7", "max_lon_e7", "max_lat_e7")])
        center = [header["center_lon_e7"] / 1e7, header["center_lat_e7"] / 1e7]
        require(0 <= header["min_zoom"] <= header["max_zoom"] <= 26, "Invalid PMTiles zoom range")
        require(0 <= header["center_zoom"] <= 26 and bounds[0] <= center[0] <= bounds[2] and
                bounds[1] <= center[1] <= bounds[3], "PMTiles center is outside its geographic bounds")
        metadata = reader.metadata()
        require(isinstance(metadata, dict), "PMTiles metadata must be an object")
        for key in ("minzoom", "maxzoom"):
            if key in metadata:
                require(number(metadata[key]) or isinstance(metadata[key], str),
                        f"Invalid PMTiles metadata {key}")
                zoom = float(metadata[key])
                require(zoom.is_integer() and zoom == header[key.replace("zoom", "_zoom")],
                        f"PMTiles metadata {key} disagrees with header")
        if "bounds" in metadata:
            declared = metadata["bounds"]
            if isinstance(declared, str):
                declared = [float(value) for value in declared.split(",")]
            validate_bounds(declared)
            require(all(abs(a - b) <= 1e-5 for a, b in zip(bounds, declared)),
                    "PMTiles metadata bounds disagree with header")
        if "center" in metadata:
            declared = metadata["center"]
            if isinstance(declared, str):
                declared = [float(value) for value in declared.split(",")]
            require(isinstance(declared, (list, tuple)) and len(declared) == 3 and
                    all(number(v) for v in declared), "Invalid PMTiles metadata center")
            require(all(abs(a - b) <= 1e-5 for a, b in zip(center, declared[:2])) and
                    declared[2] == header["center_zoom"], "PMTiles metadata center disagrees with header")
        layers = metadata.get("vector_layers")
        require(isinstance(layers, list) and layers, "Vector PMTiles metadata is missing vector_layers")
        require(all(isinstance(layer, dict) for layer in layers), "PMTiles vector_layers entries must be objects")
        ids = [layer.get("id") for layer in layers]
        require(all(isinstance(i, str) and i for i in ids) and len(ids) == len(set(ids)),
                "PMTiles source-layer IDs must be unique nonempty strings")
        missing = sorted(set(requested_layers) - set(ids))
        require(not missing, f"Missing PMTiles source layer(s) {missing}; available: {', '.join(ids)}")
        stats = {}
        for layer in layers:
            require(isinstance(layer.get("fields"), dict), f"Missing fields metadata for {layer['id']}")
            stats[layer["id"]] = {"declaredFields": layer["fields"], "geometryTypes": Counter(),
                                  "sample": Fields(), "sampledTiles": 0}
        scanned = 0
        # This is evidence of observed geometry/fields, not a logical feature count.
        for (z, x, y), tile in all_tiles(get_bytes):
            require(header["min_zoom"] <= z <= header["max_zoom"], "Tile lies outside declared zoom range")
            if header["tile_compression"] == Compression.GZIP:
                tile = gzip.decompress(tile)
            decoded = mapbox_vector_tile.decode(tile)
            for name, layer in decoded.items():
                require(name in stats, f"Tile contains undeclared source layer {name}")
                stat = stats[name]
                stat["sampledTiles"] += 1
                for feature in layer["features"]:
                    kind = feature["geometry"]["type"]
                    require(kind in FAMILIES, f"Unsupported tile geometry: {kind}")
                    stat["geometryTypes"][kind] += 1
                    stat["sample"].add(feature["properties"])
            scanned += 1
            if scanned >= sample_tiles:
                break
        result_layers = {}
        for name, stat in stats.items():
            result_layers[name] = {
                "declaredFields": stat["declaredFields"],
                "geometryTypes": dict(stat["geometryTypes"]),
                "geometryFamilies": sorted({FAMILIES[g] for g in stat["geometryTypes"]}),
                "fields": stat["sample"].result(), "sampledTiles": stat["sampledTiles"],
            }
        tilestats = metadata.get("tilestats", {})
        warnings = []
        if not header["min_zoom"] <= header["center_zoom"] <= header["max_zoom"]:
            warnings.append("Header center zoom is outside tile zoom range; do not copy it as the initial camera")
        result = {"format": "pmtiles", "version": header["version"], "tileType": "MVT",
                  "bounds": bounds, "center": center, "minZoom": header["min_zoom"],
                  "maxZoom": header["max_zoom"], "centerZoom": header["center_zoom"],
                  "layers": result_layers, "sampledTiles": scanned,
                  "samplingComplete": scanned == header["addressed_tiles_count"],
                  "metadata": metadata, "tilestats": tilestats, "errors": [], "warnings": warnings}
        for name in requested_layers:
            if not result_layers[name]["geometryTypes"]:
                result["errors"].append(f"No geometry observed for {name}; increase sample budget or inspect its tiles")
        for layer in tilestats.get("layers", []):
            name = layer.get("layer")
            require(name in result_layers, f"tilestats names an undeclared layer: {name}")
            family = FAMILIES.get(layer.get("geometry"))
            observed = result_layers[name]["geometryFamilies"]
            if family and observed:
                require(observed == [family], f"Tile geometry for {name} conflicts with tilestats")
        return result


def inspect_cog(path):
    import rasterio
    from rasterio.warp import transform_bounds
    from rio_cogeo.cogeo import cog_validate

    valid, errors, warnings = cog_validate(path, strict=True, quiet=True)
    with rasterio.open(path) as dataset:
        epsg = dataset.crs.to_epsg() if dataset.crs else None
        colors = [color.name for color in dataset.colorinterp]
        result = {"format": "cog", "cloudOptimized": valid, "epsg": epsg,
                  "bands": dataset.count, "dataTypes": list(dataset.dtypes),
                  "colorInterpretation": colors, "width": dataset.width, "height": dataset.height,
                  "blockShapes": [list(s) for s in dataset.block_shapes],
                  "overviews": dataset.overviews(1), "projectedBounds": list(dataset.bounds),
                  "bounds": list(transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds,
                                                 densify_pts=21)) if dataset.crs else None,
                  "errors": list(errors), "warnings": list(warnings)}
        if not valid:
            result["errors"].append("TIFF failed strict structural COG validation (not just a filename/tag check)")
        if epsg != 3857:
            result["errors"].append(f"Unsupported COG projection EPSG:{epsg}; Fabric Maps requires EPSG:3857")
        if colors not in (["red", "green", "blue"], ["red", "green", "blue", "alpha"]):
            result["errors"].append(f"Expected 3-band RGB or 4-band RGBA, found {colors}")
        if any(dtype != "uint8" for dtype in dataset.dtypes):
            result["errors"].append("This direct RGB imagery path requires Byte/uint8 display bands; "
                                    f"found {list(dataset.dtypes)}")
        if result["bounds"] is not None:
            validate_bounds(result["bounds"])
        return result


def require_fields(report, fields, numeric=()):
    available = report.get("fields", {})
    for name in fields:
        require(name in available, f"Field {name!r} is unavailable; available fields: {', '.join(available)}")
        types = set(available[name]["types"]) - {"null"}
        require(types and types <= {"string", "number", "boolean"},
                f"Field {name!r} has no usable scalar values")
    for name in numeric:
        require_fields(report, [name])
        require(set(available[name]["types"]) - {"null"} == {"number"},
                f"Field {name!r} must be numeric")


def require_geometry(report, family):
    require(family in ("point", "line", "polygon"), f"Unknown requested geometry family {family}")
    families = report.get("geometryFamilies", [])
    require(families == [family],
            f"Requested {family} layer but found {', '.join(report.get('geometryTypes', {})) or 'no observed geometry'}; "
            "do not silently change the requested layer type")


def inspect(path, **kwargs):
    path = Path(path)
    with path.open("rb") as source:
        magic = source.read(16)
    if magic.startswith(b"PMTiles"):
        return inspect_pmtiles(path, kwargs.get("layers", ()), kwargs.get("sample_tiles", 256))
    if magic[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return inspect_cog(path)
    require(path.suffix.lower() in (".json", ".geojson") or magic.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"{"),
            "Unrecognized geospatial content; extension alone does not prove a supported format")
    return inspect_geojson(path, kwargs.get("filters", ()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--layer", action="append", default=[])
    parser.add_argument("--field", action="append", default=[])
    parser.add_argument("--numeric-field", action="append", default=[])
    parser.add_argument("--geometry", choices=["point", "line", "polygon"])
    parser.add_argument("--filters", default="[]", help='Inspection predicates, e.g. [{"field":"STATE","op":"eq","value":"53"}]')
    parser.add_argument("--sample-tiles", type=int, default=256)
    args = parser.parse_args()
    try:
        report = inspect(args.path, layers=args.layer, filters=json.loads(args.filters),
                         sample_tiles=args.sample_tiles)
        selected = [report["layers"][name] for name in args.layer] if report["format"] == "pmtiles" else [report]
        require(not (report["format"] == "pmtiles" and not args.layer and
                     (args.field or args.numeric_field or args.geometry)),
                "Select --layer before validating vector PMTiles bindings")
        for layer in selected:
            require_fields(layer, args.field + args.numeric_field, args.numeric_field)
            if args.geometry:
                require_geometry(layer, args.geometry)
        print(json.dumps(report, indent=2, allow_nan=False))
        return 2 if report["errors"] else 0
    except (ValidationError, OSError, ValueError, ijson.JSONError) as error:
        print(json.dumps({"path": str(args.path), "errors": [str(error)]}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
