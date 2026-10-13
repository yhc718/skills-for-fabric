# Eventhouse source adapter

For authoring, read this after `../authoring.md` when a layer uses KQL results.
For capability questions, read the summary only; no source lookup is needed.
The shared reference owns lifecycle, encoding, styling, updates, and readback.

## Capabilities

Eventhouse layers use data from a KQL Database. Supported entities are tables,
materialized views, and stored, parameterless tabular functions. Spatial data
can use either of two modes:

- Numeric latitude and longitude columns for point locations.
- A dynamic GeoJSON geometry column for points, lines, or polygons.

Compatible results support bubbles, markers, heatmaps, line and polygon
styling, and polygon extrusions. These are vector layers, not raster imagery.
Automatic refresh updates query results while retaining saved layer settings.

Results must not exceed **100,000 features or 20 MB**, independently. A display
filter does not reduce the source result subject to these limits. Direct
**Show Table** previews display only the last 100 ingested rows; this preview
limit does not establish the full layer count. Functions and materialized views
can provide aggregated or filtered results.

Identify the KQL Database from the request, existing Map, or resolved variable
target. Resolve the entity and inspect its output before asking for unresolved
entity or spatial-field choices. Discover metadata rather than requesting it
as setup input.

Map authoring does not modify KQL entities or source data. Report validation
and service errors with the affected entity or layer and the returned
code/message, when available. Stop the failed workflow without repairing data,
changing geometry, or substituting a different source. An existing compatible
entity can be suggested after verification; creating or changing one belongs
to a separately requested Eventhouse authoring workflow.

## Resolve workspace, Eventhouse, database, and entity

Use [eventhouse-cli](../../../eventhouse-cli/SKILL.md) in **consumption** mode
and read its connection/discovery references before querying. All source
operations here are read-only. The Map references the **KQL Database item**,
not its parent Eventhouse or an Eventstream. Reuse supplied IDs and follow
the shared pagination, exact-match, and authentication rules.

| Resolution | Read-only operation |
|---|---|
| Workspace | Verify a supplied URL/ID with `GET /v1/workspaces/{id}`; otherwise paginate workspaces and exact-match `displayName` |
| Eventhouse | Paginate `GET /v1/workspaces/{workspaceId}/eventhouses`; verify the selected ID |
| KQL Database | Paginate `GET /v1/workspaces/{workspaceId}/kqlDatabases`; get the selected database's full metadata |
| Parent and endpoint | Verify `properties.parentEventhouseItemId` against the resolved Eventhouse; use `properties.queryServiceUri`, never an ingestion endpoint |
| Database query name | Resolve `.show databases` against the verified item ID/unique pretty name; use returned `DatabaseName`, which may be a GUID, not `displayName` |
| Entities | Run `.show tables`, `.show functions`, and `.show materialized-views`; retain their distinct types |

Resolve within the supplied scope: follow a database's parent reference, or
restrict database discovery to the selected Eventhouse's children. Ask for
selection only when the target remains ambiguous.

Use the Kusto audience `https://kusto.kusto.windows.net` and POST read-only KQL
to the discovered URI's `/v1/rest/query`, `.show` commands to `/v1/rest/mgmt`.
Use body files with `az rest`, not shell-interpolated inline KQL. Escape entity
and column identifiers; do not interpolate user text as a KQL expression.
Check request errors and QueryStatus as well as HTTP status. Partial failures,
truncation, and malformed rows make the result incomplete and block writing.

Resolve entity type from the inventories, not naming conventions. Inspect
function Parameters/Body/DocString and view SourceTable/Query, and confirm the
entity's actual output with `getschema`. Invoke stored, parameterless tabular
functions with parentheses.

Name unresolved entities and list available tables, functions, and views after
complete discovery. Access failures or incomplete inventories do not establish
that an entity is missing. Perform **no Map write** for unresolved sources.

## Inspect the selected sources

Inspect each entity independently. A schema or bounded sample helps discover
fields; it does not validate the complete result.

- Run `<entity> | getschema` and a bounded sample. Bound raw temporal queries
  with an applicable time window based on the request and measured timestamp
  range. Static geographic data does not require an invented timestamp.
- Measure the full intended entity count and result size before a preview
  limit, geometry exclusion, or Map filter. Report results exceeding either
  documented limit; verify any suggested alternative independently.
- Execute the complete result within those limits, projecting the spatial,
  identity, label, tooltip, color, size, and filter fields. Do not save a
  preview limit as a way to truncate an oversized source. Reconcile counted
  and returned rows; changing data requires a consistent read or revalidation.
- Inventory field types, null/empty values, categories, and numeric ranges.
  Validate requested fields against the actual result. Suggest real fields
  on a miss; do not invent aliases or labels for unnamed features.
- Record full, usable, and Map-selected counts and bounds separately. Derive
  the initial view from selected valid geometry using the shared camera rules.
  Empty results or selections require clarification before an empty layer
  can be created.

For latest-state functions, verify the entity key, timestamp semantics, and
exact key uniqueness rather than approximate distinct counts. Preserve the
existing reduction and freshness policy; an additional time filter can remove
valid but inactive entities. Changing that policy requires clarification.

For comparison-only requests, answer the requested analysis from query results,
with exact counts and a clear denominator. Suggest a Map using existing spatial
fields without creating or changing anything.

### Latitude/longitude mode

Discover both columns and their Kusto types. Prefer an unambiguous existing
pair; names alone do not establish validity. Require numeric types, including
explicitly converted output from an existing function. Numeric-looking strings
are not numeric columns. Report conversion failures and usable counts before
recommending a verified conversion function; do not change or substitute the
source.

Check every row for finite numbers, latitude -90..90, longitude -180..180,
null/missing values and the 0,0 placeholder. Report mutually exclusive invalid
counts and affected identities, distinguishing reversed coordinates from other
range errors. Confirm coordinate order using column meaning and geographic
scope; valid ranges alone do not resolve an ambiguous pair. Do not swap, clamp,
geocode, or invent coordinates.

Exclude invalid rows in the **Map-owned** query with a predicate using verified
numeric columns, for example:

```kql
<verified entity>
| where isfinite(Latitude) and isfinite(Longitude)
| where Latitude between (-90.0 .. 90.0) and Longitude between (-180.0 .. 180.0)
| where not(Latitude == 0 and Longitude == 0)
| project <verified required fields>
```

Only valid coordinates contribute to camera bounds. Set
`layerSettings[].latitudeColumnName` and `longitudeColumnName`; omit
`geometryColumnName`. Coordinate pairs represent **points**, never lines/areas.

### Single geometry-column mode

Require a Kusto `dynamic` column containing GeoJSON geometry objects. Decode
dynamic JSON transport strings without treating a Kusto `string` WKT column as
geometry. Prefer a verified function already applying `geo_from_wkt` over raw
WKT text. Inspect the function output, not merely its body.

Validate geometry type, coordinate nesting, finite WGS84 positions, line/ring
lengths and closed polygon rings. Count null, empty and invalid shapes and name
the affected rows using existing identifiers/names. Do not repair shapes or
silently convert polygons/lines to points. Set
`layerSettings[].geometryColumnName`; omit both coordinate mappings.

Report counts per geometry family. A requested geometry family must match
**all** usable geometry before display filtering. For mixed-geometry Maps,
preserve every valid family and use its corresponding rendering settings.
Do not silently discard valid shapes to satisfy a different layer type.

Null geometry from failed WKT conversion can be excluded with
`where isnotnull(<geometry>)`; empty coordinates need a verified nonempty-array
check too. Exclusion predicates must remain valid on refresh; preview row
positions are not stable identifiers. Report malformed nonnull shapes and
stop when safe exclusion cannot be validated.

### Before writing

Complete source, geometry, field, filter, layer-order, and camera validation
before the first mutation. Execute and revalidate the final Map-owned query,
including projections and geometry exclusions. Retain the original invalid-row
counts and reasons alongside the final usable result. Check transport size
conservatively at 20,000,000 bytes and verify rendered dataset size as well.

Use read-only queries and available geometry readers; incomplete decoding or
validation blocks writing. Report errors and warnings rather than treating
them as a successful inspection. For multi-source requests, a failed input
blocks the whole intended write; do not create a partial or blank Map instead.

## Definition parts

Derive the reference branch from the operation's selected schema using the
shared schema-selection rules. For a new Map whose schema supports the
nondeprecated item-reference model, use the resolved KQL Database item ID as
the stable `datasourceId` for a direct database reference:

```json
{
  "datasourceId": "<resolved-kql-database-guid>",
  "item": {
    "workspaceId": "<workspace-guid>",
    "itemId": "<kql-database-guid>"
  }
}
```

Link the Kusto layer source through that same data source:

```json
{
  "id": "<stable-layer-source-id>",
  "name": "<source name>",
  "type": "kusto",
  "datasourceId": "<resolved-kql-database-guid>"
}
```

If the selected schema lacks that branch, use only the legacy workspace-item
and layer-link fields that its applicable alternatives require. Do not combine
the two representations. Preserve an existing Map's representation unless a
schema migration is explicitly requested.

Preserve existing aliases during unrelated edits; the direct-item convention
does not impose a UUID requirement on every string identifier.

The associated query is a separate UTF-8 definition part:

```text
queries/layerSource-<stable-layer-source-id>.kql
```

The identifier in the filename must exactly match `layerSources[].id`.
Base64-encode the KQL file like every other definition part.

Assemble the Kusto source and encoded query part from the **executed final
query** and its complete, valid inspection evidence. Reuse the geometry-family
styling, text/numeric filters and camera procedure in
[the shared authoring reference](../authoring.md#shared-data-layer-settings),
as for Lakehouse layers. Validate the complete document against the operation's
selected schema; source fragments alone do not establish validity.

Use a verified table/view reference or parameterless function invocation.
Embedded KQL Querysets are deprecated; a query definition part is not a Queryset
item. Report service rejections without silently changing the source or using
a different definition contract.

## Layer settings and mixed sources

Regional and analytical filters live in `layerSettings[].filters`, not in
stored KQL entities. Preserve leading-zero text codes. Select administrative
areas by their membership field rather than bounding-box intersection, and
apply the intended scope to every relevant layer. Compute selected bounds
before fitting the camera; preserve the saved view on unrelated edits.

Use the shared geometry-compatible styling, filter, visibility, and draw-order
rules. Verify requested rendering against the selected schema and current
product capabilities. Report unsupported styling before writing; do not
change geometry or substitute another visual treatment to approximate it.

A mixed Eventhouse/Lakehouse Map keeps independent data sources, Kusto query
parts, and exact Lakehouse file paths. Each adapter validates its own input;
shared settings control styling, order, and the initial view.

For spatial comparisons, validate the requested relationship against the
actual geometries. Report data discrepancies separately from source-loading
or rendering errors. Preserve source locations and boundaries; Map authoring
does not correct disagreements between datasets.

## Refresh

Save requested refresh intervals in `layerSources[].refreshIntervalMs`, using
milliseconds. Omit an unspecified interval on creation and preserve it on
edits; zero does not mean continuous streaming.

Patch only requested refresh and layer settings in the retrieved full
definition. Preserve unrelated IDs, query parts, geometry mappings, styling,
labels, filters, visibility, order, and camera. Refresh is source-scoped:
confirm the intended scope when a change also affects other layers sharing
that source.

## Readback

After the shared terminal write:

- decode `map.json` and the matching `.kql` part;
- verify source item ID, selected data-source branch and link, layer source ID,
  query filename, and any configured refresh interval;
- compare the persisted query text semantically with the executed query;
- compare labels, tooltips, color/size fields and expressions, geometry mapping,
  filters, visibility, draw order and camera against the intended settings;
- on edits, compare all unrelated settings and part payloads to the baseline;
- verify the intended result count independently of a table preview.

REST readback proves saved configuration, not rendering or automatic refresh.
When the Map can be opened, verify layers at the saved view, observe a configured
refresh, and reopen to check persistence. A separate source query is not
evidence that the Map refreshed. Mark checks that cannot be observed as
unverified, and report service errors as returned without turning
environment-specific behavior into general capability restrictions.

Use consumption mode for explanations. Attribute counts to query evidence
and distinguish full, usable, and selected results from the saved initial
view; explaining a Map does not authorize queries or changes.

## References

- Kusto entities and limits:
  https://learn.microsoft.com/fabric/real-time-intelligence/map/about-kusto-integration
- Geometry modes and 100,000-feature result limit:
  https://learn.microsoft.com/fabric/real-time-intelligence/map/create-layers-using-kusto-data
- Partial-result failure handling:
  https://learn.microsoft.com/kusto/concepts/partial-query-failures
- Fabric Maps overview:
  https://learn.microsoft.com/fabric/real-time-intelligence/map/about-fabric-maps
- Map definition:
  https://learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/map-definition
