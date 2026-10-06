"""Source fragments and shared layer settings; no network or Map writes.

Validate the complete assembled document against its runtime-selected schema.
These helpers create new entries only; edits must patch a fetched baseline.
"""

import json
import uuid

from inspect_lakehouse import require, require_fields, require_geometry


COLORS = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#332288"]


def category_key(value):
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return json.dumps(value, allow_nan=False)


def source_fragment(report, source_id, name, datasource_id, relative_path):
    require(not report.get("errors"), "; ".join(report.get("errors", [])))
    require(report["format"] in ("geojson", "pmtiles", "cog"), "Unsupported source format")
    require(relative_path.startswith("Files/") and
            all(part not in ("", ".", "..") for part in relative_path.split("/")) and
            "\\" not in relative_path, "Use the exact resolved Files/... OneLake path")
    return {"id": source_id, "name": name, "type": report["format"],
            "datasourceId": datasource_id, "relativePath": relative_path}


def text_filter(report, field, values, locked=True):
    require_fields(report, [field])
    require(set(report["fields"][field]["types"]) - {"null"} == {"string"},
            f"{field} is not a text field; do not coerce numeric codes")
    require(values and all(isinstance(v, str) for v in values), "Choose real text filter values")
    stat = report["fields"][field]
    if not stat["valuesTruncated"]:
        require(all(v in stat["values"] for v in values), f"Unresolved values for {field}: {values}")
    return {"id": str(uuid.uuid4()), "type": "text", "field": field,
            "locked": locked, "value": list(values)}


def number_filter(report, field, minimum, maximum, locked=True):
    require_fields(report, [field], [field])
    from inspect_lakehouse import number
    require(number(minimum) and number(maximum) and minimum <= maximum, "Invalid inclusive numeric range")
    return {"id": str(uuid.uuid4()), "type": "number", "field": field,
            "locked": locked, "min": minimum, "max": maximum}


def vector_layer(report, source_id, name, family, *, source_layer=None, label=None,
                 color_by=None, size_by=None, tooltips=(), color="#0072B2",
                 opacity=0.65, filters=(), category_colors=None):
    require(not report.get("errors"), "; ".join(report.get("errors", [])))
    require(report["format"] in ("geojson", "pmtiles"), "Vector layer requires vector data")
    if report["format"] == "pmtiles":
        require(source_layer in report["layers"],
                f"Missing PMTiles layer {source_layer!r}; available: {', '.join(report['layers'])}")
        evidence = report["layers"][source_layer]
    else:
        require(source_layer is None, "Non-tile sources must not specify a vector-tile source layer")
        evidence = report
    require_geometry(evidence, family)
    require(0 <= opacity <= 1, "Opacity must be in 0..1")
    require_fields(evidence, [f for f in (label, color_by, size_by) if f] + list(tooltips),
                   [size_by] if size_by else [])
    require(not size_by or family == "point", "Data-driven bubble sizing requires point geometry")
    require(category_colors is None or color_by, "A category palette requires a color field")
    options = {"type": "vector", "visible": True, "color": color, "enablePopups": True}
    if family == "point":
        options["pointLayerType"] = "bubble"
        style = {"color": color, "opacity": opacity, "strokeColor": "#FFFFFF", "strokeWidth": 1}
        style.update({"sizeType": "data-driven", "sizeProperty": size_by} if size_by else
                     {"sizeType": "fixed", "fixedSize": 6})
        options["bubbleOptions"] = style
    elif family == "line":
        style = {"strokeColor": color, "strokeOpacity": opacity, "strokeWidth": 2}
        options["lineOptions"] = style
    else:
        style = {"fillColor": color, "fillOpacity": opacity}
        options["polygonOptions"] = style
    if color_by:
        values = sorted((v for v in evidence["fields"][color_by]["values"] if v is not None),
                        key=lambda value: (type(value).__name__, str(value)))
        palette = category_colors if category_colors is not None else {
            category_key(value): COLORS[i % len(COLORS)] for i, value in enumerate(values)}
        require(isinstance(palette, dict) and palette and
                all(isinstance(k, str) and isinstance(v, str) and v for k, v in palette.items()),
                "Category colors must map category strings to color strings")
        require(all(category_key(value) in palette for value in values),
                "Shared palette is missing observed categories")
        style.update({"enableSeriesGroup": True, "seriesGroup": color_by,
                      "customColors": dict(palette)})
        expression = ["match", ["to-string", ["get", color_by]]]
        for value, category_color in palette.items():
            expression.extend([value, category_color])
        expression.append(color)
        style[{"point": "color", "line": "strokeColor", "polygon": "fillColor"}[family]] = expression
    if label:
        options["dataLabelOptions"] = {"enabled": True, "size": 12}
        options["dataLabelKeys"] = [label]
    options["tooltipKeys"] = list(dict.fromkeys([f for f in (label, color_by, size_by) if f] + list(tooltips)))
    layer = {"id": str(uuid.uuid4()), "name": name, "sourceId": source_id, "options": options}
    if source_layer is not None:
        layer["sourceLayerId"] = source_layer
        options["sourceLayer"] = source_layer
    if filters:
        require_fields(evidence, [f["field"] for f in filters])
        layer["filters"] = list(filters)
    return layer


def raster_layer(report, source_id, name, opacity=1):
    require(not report.get("errors"), "; ".join(report.get("errors", [])))
    require(report["format"] == "cog", "Use an independently validated imagery source")
    require(0 <= opacity <= 1, "Opacity must be in 0..1")
    return {"id": str(uuid.uuid4()), "name": name, "sourceId": source_id,
            "options": {"type": "raster", "visible": True, "opacity": opacity}}
