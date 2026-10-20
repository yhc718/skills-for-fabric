# Connection source adapter

For authoring, read this after `../authoring.md` when a layer references a Fabric
Connection. For capability questions, read the summary without listing tenant
connections or running setup.

## Documented Map adapters

| Connection type (Fabric UI) | Data and rendering | Limits |
|---|---|---|
| Geospatial Web Services | WMS / WMTS imagery or WFS, OGC API - Features, and Esri Feature Service vectors | Public HTTPS service endpoint; select a published layer or collection |
| Microsoft Planetary Computer | Microsoft Planetary Computer Pro (MPC Pro) GeoCatalog imagery through WMTS | Entra OAuth 2.0; connection creation requires GeoCatalog Reader access and a nonguest tenant account |

These are distinct documented UI connection types; do not derive REST connector
type identifiers from their labels. Resolve the existing connection's metadata.
WMS/WMTS imagery requires EPSG:3857 and JPEG or PNG images; do not apply these
imagery checks to feature services. Feature services return geometries and
attributes for shared vector styling, labels, tooltips, and filtering.
Imagery and vector layers can coexist. Consult the current Map documentation
and selected schema for supported protocols, authentication, limits, and
renderings; this table is not an exhaustive connector catalog or proof of
tenant availability.

Identify the existing connection from the request, existing Map, or resolved
variable target. Inspect read-only metadata for its endpoint, protocol, and
published resources, including the GeoCatalog and WMTS resource for MPC Pro.
Ask only for an unresolved layer or collection selection; do not
request discoverable endpoint details or invent IDs from display names.

## Scope and ownership

Prefer existing Fabric Connections. Creation, credential replacement/rotation,
and provider registration require explicit connection-administration
authorization, separate from Map CRUD.
Never reveal credentials.

Fabric Map definitions store connection IDs and provider resource identifiers,
not secrets.

For authorized creation, discover the connector's creation methods, parameter
types/allowed values, and credential support through the public
`GET /v1/connections/supportedConnectionTypes` API, following all pages.
Use the current public create-connection contract and verify the created
connection before Map authoring. Do not derive API identifiers from UI labels,
guess protocol values, skip connection validation, or substitute private APIs
when public API support is unavailable.

## Resolution and validation

1. List Fabric Connections with pagination using `GET /v1/connections`, the
   Fabric audience, and the skill telemetry header.
2. Exact-match the intended connection by display name and compatible
   connection type.
3. If more than one connection matches, ask the user to choose.
4. Resolve the actual protocol and resource identifier using read-only provider
   metadata. Validate imagery format/projection or feature-service compatibility
   as appropriate; a display name is not a resource identifier.
5. Never print credentials, tokens, keys, or full secret-bearing connection
   payloads.

Connections are tenant-scoped resources; do not assume a workspace association
that the API does not expose.

### Feature-service preflight

Follow the current [feature-service setup and metadata guidance](https://learn.microsoft.com/fabric/real-time-intelligence/map/add-external-feature-service-layer)
for the selected protocol. Verify service capabilities/conformance, the exact
layer or collection, queryable fields, geometry, paging, and response formats.
Use the public HTTPS service root for the connection, not a query URL; query
parameters in the connection URL are not a saved source query.

- Require a supported GeoJSON response in EPSG:4326 longitude/latitude order,
  including service-side reprojection when supported. Do not apply imagery
  EPSG:3857 checks or silently convert source data.
- Validate source filters and output fields against the service's advertised
  capabilities and actual results. Retrieve the fields needed by shared
  styling and display filters; numeric-looking text is not numeric data.
- Account for every result page, service limits, null/unsupported geometry,
  and the inspected spatial scope. Partial responses, truncation, or request
  failures are not complete results. Source-query counts can differ from
  viewport-dependent Map retrieval; display filters cannot repair truncation.
- Preserve valid geometry families; use the documented mixed-geometry behavior.
  Do not assume clustering or extrusions work for mixed geometry, or that a
  protocol supports it merely because another protocol does.

Provider inspection requires its own authorized access. A Fabric connection
ID does not expose its credentials or authorize direct requests using a Fabric
token. Report unavailable inspection as a blocker rather than bypassing it.
Do not create a blank or partial Map after failed preflight.

### Retrieval, display, and refresh

Source queries control remote retrieval; map-layer filters control display
of retrieved features. Keep these stages separate and use the protocol's
documented query language, not Map filter JSON, for source queries.
The Map derives retrieval bounds from its viewport; verify the documented
spatial restrictions rather than inventing saved bounding-box settings.

Preserve saved source queries and output fields during visual edits. Follow
the documented workflow and restrictions for changing retrieval; schema
acceptance alone does not authorize unsupported query-edit or refresh behavior.
Duplicated layers may share retrieval while retaining independent visual
settings and display filters. Distinguish metadata refresh from data refresh;
neither authorizes changes to the remote service.

## Definition fragments

Derive the reference branch from the operation's selected schema using the
shared schema-selection rules. For a new Map whose schema supports the
nondeprecated connection-reference model, use a stable `datasourceId`:

```json
{
  "datasourceId": "external-source",
  "connection": {
    "connectionId": "<connection-guid>"
  }
}
```

Link the connection layer source through that same data source:

```json
{
  "id": "<stable-layer-source-id>",
  "name": "<source name>",
  "type": "connection",
  "datasourceId": "external-source",
  "connectionResourceId": "<provider resource identifier>"
}
```

If the selected schema lacks that branch, use only the legacy connection and
layer-link fields that its applicable alternatives require. Do not combine the
two representations. Preserve an existing Map's representation unless a schema
migration is explicitly requested.

Use the exact source type and resource identifier supported by the selected
connection/provider and current schema. Do not infer them from a display name.
Select raster or vector rendering from the verified protocol and data, not
the connection type alone. Use the matching layer-source ID and shared
[data-layer settings](../authoring.md#shared-data-layer-settings); do not
duplicate styling here or apply tile-layer bindings to feature services.
Serialize feature-service retrieval using the selected schema's documented
source-query structure, not a KQL part or invented fields.

Authentication depends on the protocol and current connector support.
Credentials stay in the Fabric Connection, never in a Map definition.

## Security rules

- Do not retrieve or display secrets to resolve a connection.
- Do not embed secrets, SAS tokens, API keys, or bearer tokens in `map.json`.
- Stop if a provider requires a secret in the definition rather than a Fabric
  Connection reference.
- Treat connection replacement as a semantic source change and validate the
  provider resource before the Map update.

## Readback

After the shared terminal write, verify that the persisted data source and layer
source contain the intended connection ID, selected reference branch and link,
and resource ID. For feature services, compare the source query/output fields
separately from display filters and shared vector settings. Preserve unrelated
Map state, IDs, and parts through the shared read-modify-write procedure.
Verify rendering after reopening, including geometry, attributes, legends,
and any partial-result warnings. Report connection access, provider-resource
failures, and unobserved rendering separately from definition persistence.

## References

- [WMS/WMTS and MPC Pro capabilities](https://learn.microsoft.com/fabric/real-time-intelligence/map/about-external-sourced-imagery)
- [Connection setup and MPC Pro prerequisites](https://learn.microsoft.com/fabric/real-time-intelligence/map/add-external-sourced-imagery-layer)
- [Feature-service capabilities and restrictions](https://learn.microsoft.com/fabric/real-time-intelligence/map/about-external-feature-services)
- [Feature-service setup and metadata checks](https://learn.microsoft.com/fabric/real-time-intelligence/map/add-external-feature-service-layer)
- [Discover connection creation contracts](https://learn.microsoft.com/rest/api/fabric/core/connections/list-supported-connection-types)
- [Create connections](https://learn.microsoft.com/rest/api/fabric/core/connections/create-connection)
- [List connections](https://learn.microsoft.com/rest/api/fabric/core/connections/list-connections)
