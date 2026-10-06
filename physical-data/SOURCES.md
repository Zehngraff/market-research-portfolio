# Method and provider notes

This module is a public-safe extraction of existing project ETL and feature routines, with new input contracts, a CLI and offline tests. It is not a reproduction of the full research project or an independent validation of its forecasting results. The reviewed code snapshot predates any unavailable later local changes; those are not represented here.

## Authoritative reference documentation

- [ECMWF: ERA5-Land data documentation](https://confluence.ecmwf.int/spaces/CKB/pages/140385202/ERA5-Land+data+documentation) explains the cumulative hourly precipitation convention and next-day midnight boundary. The input convention matters: ERA5-Land accumulations must not be treated like hourly increments.
- [STAC API Item Search specification](https://github.com/radiantearth/stac-api-spec/blob/main/item-search/README.md) defines search and next-link GET/POST pagination.
- [STAC Raster extension](https://github.com/stac-extensions/raster) defines band-level scale, offset and nodata metadata. This implementation supports the documented Raster v1 and v2 shapes, and requires an explicit transform rather than assuming one.
- [Element 84 Earth Search](https://earth-search.aws.element84.com/v1) is the optional Sentinel-2 catalogue endpoint. No image assets from this service are included.
- [Eurostat API documentation](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction) describes the optional official public-statistics API. No official Eurostat observations are included in the fixture.

The implementation does not bundle code copied from the above documentation or third-party libraries. The source extraction retained the project author's methods; the underlying source files carried no additional third-party code notices in the selected functions.

## Fixtures and data reuse

Every file under `fixtures/synthetic` is invented deterministically by the included generator. IDs prefixed `synthetic` and the `XX_A`/`XX_B` statistical areas do not denote real fields or official statistical regions. Example latitude/longitude bounds are neutral query examples and are not linked to company parcels. All files under `examples/output` are derived from these synthetic inputs. Snapshot timestamps are fixed fixture metadata, not claims that a live request occurred then.

Access to a public API does not automatically grant rights to redistribute every response or raster asset. Consult each provider's current terms/licence and collection metadata before using or redistributing real data. This package makes no blanket data-licence grant, embeds no provider credentials and selects no project software licence.
