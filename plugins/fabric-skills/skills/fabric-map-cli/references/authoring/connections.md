# Connection source adapter

For authoring, read this after `../authoring.md` when a layer references a Fabric
Connection. For capability questions, read the summary without listing tenant
connections or running setup.

## Documented Map adapters

| Connection type (Fabric UI) | Data |
|---|---|
| Geospatial Web Services | External imagery and vector feature services |
| Microsoft Planetary Computer | Microsoft Planetary Computer Pro (MPC Pro) GeoCatalog imagery |

Feature services supply geometries and attributes for shared vector styling,
labels, tooltips, and filtering. Imagery and vector layers can coexist.
Resolve current protocols, authentication, projection, formats, geometry
support, and service limits from the public references below and the selected
Map schema. Use the shared capability-discovery and schema-resolution rules.

## Scope and ownership

Reuse existing Fabric Connections. Connection creation, credential updates,
and provider registration require explicit connection-administration
authorization, separate from Map authoring.

For authorized creation, discover the connector's creation methods, parameter
types/allowed values, and credential support through the public
`GET /v1/connections/supportedConnectionTypes` API, following all pages.
Use the current public create-connection contract and verify the created
connection before Map authoring.

Keep credentials in the Fabric Connection and out of definitions, logs, and
responses. Map definitions contain connection references and provider resource
identifiers. Inspect providers through their own authorized access; keep Fabric
API credentials scoped to Fabric. Validate a replacement connection as a source
change.

## Resolution and validation

1. Identify the connection from the request, existing Map, or resolved variable
   target. Discover tenant-scoped connections with paginated
   `GET /v1/connections`, using the Fabric audience and skill telemetry header.
2. Resolve one connection by exact display name and compatible type. Clarify
   ambiguous matches with the user.
3. Read connection and provider metadata to resolve the actual endpoint,
   protocol, and published resource identifiers.
4. Validate the selected resource using its source-specific requirements.
   Ask only for unresolved layer or collection choices.

### Feature-service preflight

Follow the current [feature-service setup and metadata guidance](https://learn.microsoft.com/fabric/real-time-intelligence/map/add-external-feature-service-layer).
Use the published HTTPS service root as the connection endpoint and configure
retrieval through the source query.

- Verify protocol metadata, capabilities, and the selected layer or collection
  against actual service responses.
- Validate geometry, coordinates, and response formats using the selected
  protocol's requirements, including its mixed-geometry behavior.
- Validate source filters and output fields against advertised capabilities
  and actual field types. Include attributes needed by styling and filtering.
- Account for paging, service limits, and geometry coverage within the
  requested query and spatial scope.

Complete source validation before creating or changing a data-backed Map.

### Retrieval and display

Source queries control remote retrieval; map-layer filters control which
retrieved features are displayed. Use the provider's documented query language
for source queries and the shared Map filter structure for display filters.
Account for viewport-dependent retrieval according to the current source
guidance.

Preserve source queries and output fields during visual edits. Use the
documented source workflow for retrieval changes. Duplicated layers share
retrieval configuration while retaining independent visual settings and display
filters.

## Definition fragments

Resolve the definition structure from the selected schema using the shared
schema-selection rules. The connection-reference model links through a stable
`datasourceId`:

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

Use source types and resource identifiers verified from the provider and
selected schema. Preserve existing representations and IDs through the shared
read-modify-write procedure; schema migration is a separate requested operation.

Select raster or vector rendering from the verified protocol and data. Link
the layer-source ID to the shared
[data-layer settings](../authoring.md#shared-data-layer-settings).
Serialize feature-service retrieval using the selected schema's source-query
structure. Reuse shared vector styling and filtering.

## Readback

After the shared terminal write, verify connection and resource identifiers,
source links, queries, and output fields separately from display filters and
vector settings. Confirm preservation of unrelated Map state, IDs, and parts.
Verify geometry, attributes, legends, and retrieval completeness in the Map for
the inspected query and spatial scope. Report definition persistence, source
retrieval, and rendering separately, based on observed results.

## References

- [WMS/WMTS and MPC Pro capabilities](https://learn.microsoft.com/fabric/real-time-intelligence/map/about-external-sourced-imagery)
- [Connection setup and MPC Pro prerequisites](https://learn.microsoft.com/fabric/real-time-intelligence/map/add-external-sourced-imagery-layer)
- [Feature-service capabilities and restrictions](https://learn.microsoft.com/fabric/real-time-intelligence/map/about-external-feature-services)
- [Feature-service setup and metadata checks](https://learn.microsoft.com/fabric/real-time-intelligence/map/add-external-feature-service-layer)
- [Discover connection creation contracts](https://learn.microsoft.com/rest/api/fabric/core/connections/list-supported-connection-types)
- [Create connections](https://learn.microsoft.com/rest/api/fabric/core/connections/create-connection)
- [List connections](https://learn.microsoft.com/rest/api/fabric/core/connections/list-connections)
