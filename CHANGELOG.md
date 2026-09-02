# Changelog

## [0.5.0](https://github.com/cveld/terraform-importer/compare/v0.4.0...v0.5.0) (2026-09-02)


### Features

* support azurerm_app_service_managed_certificate and certificate_binding ([a633cff](https://github.com/cveld/terraform-importer/commit/a633cff0b7fb0cc2960ab3dabb9fe419cf9fdf1c))
* support terraform_data, resource_provider_registration, VM extension ([#10](https://github.com/cveld/terraform-importer/issues/10)) ([f3ae8bb](https://github.com/cveld/terraform-importer/commit/f3ae8bbab100fdedf26f6807789c292749febdd8))
* trace principal_id to sibling UAMI, add --verify-exists gate for pending imports ([73d603b](https://github.com/cveld/terraform-importer/commit/73d603b95cebd44cd80874a31b7dbb0ccb6946e8))


### Bug Fixes

* App Service Managed Certificate name includes the site, not just the hostname ([5f14e74](https://github.com/cveld/terraform-importer/commit/5f14e7477327bfb3b2fde22a4df0f63860a6656f))

## [0.4.0](https://github.com/cveld/terraform-importer/compare/v0.3.1...v0.4.0) (2026-06-19)


### Features

* add --out sidecar output, resolve-mode flags, and full-pool --target ([9b1a49e](https://github.com/cveld/terraform-importer/commit/9b1a49ebf1169bb88ff883cb7527c55740937648))
* add cross-plan resolver for azurerm_storage_container ([0ee12a7](https://github.com/cveld/terraform-importer/commit/0ee12a7fdd6284b81f0affdfb3d67b554e1bddee))
* add ID formulas for app configuration, cosmosdb, and dns records ([1fbf1b1](https://github.com/cveld/terraform-importer/commit/1fbf1b1c234b433ac1d197c832da4ce70f7eda59))
* cache az lookups in a persistent read-through cache ([7d8b63a](https://github.com/cveld/terraform-importer/commit/7d8b63a8ba22fb69c99f3805a3390d53045faed6))
* fall back to tfstate for subscription-id when root var has no default ([1afdaee](https://github.com/cveld/terraform-importer/commit/1afdaee1db524c526165fe71f481914d6a077526))
* resolve role assignments built from a for_each role_assignments map ([5118445](https://github.com/cveld/terraform-importer/commit/5118445f410ede8c630e750fda4a81f0c5406cb1))
* trace cross-module references to resolve key vault id ([cd595a9](https://github.com/cveld/terraform-importer/commit/cd595a9bb13720161a78d03e7c52e65e457c1ee2))


### Bug Fixes

* resolve role assignment scope cross-plan and bypass graph lookup ([d7401bc](https://github.com/cveld/terraform-importer/commit/d7401bc82cddc504e9a9eb1249c7b753886595df))


### Documentation

* capture tfstate subscription fallback and full resolution layers ([bd72ea6](https://github.com/cveld/terraform-importer/commit/bd72ea6b29bc052b193d8c883763f7333b65f063))
* describe cross-module reference tracing in resolvers.md ([8d29568](https://github.com/cveld/terraform-importer/commit/8d295689b3395a3d3688737d326b8de84c3396ea))

## [0.3.1](https://github.com/cveld/terraform-importer/compare/v0.3.0...v0.3.1) (2026-06-11)


### Bug Fixes

* fall back to role_definition_name when role_definition_id is computed ([b359c26](https://github.com/cveld/terraform-importer/commit/b359c268bf3f2c582c41afb156b67870dfc12b1e))

## [0.3.0](https://github.com/cveld/terraform-importer/compare/v0.2.1...v0.3.0) (2026-06-08)


### Features

* cross-plan ID resolution and per-resource subscription resolution ([c8b7114](https://github.com/cveld/terraform-importer/commit/c8b71144a1cbcdf8e0a7b084178e570a76207a7d))

## [0.2.1](https://github.com/cveld/terraform-importer/compare/v0.2.0...v0.2.1) (2026-06-02)


### Bug Fixes

* add missing live resolver for azurerm_role_assignment ([9833904](https://github.com/cveld/terraform-importer/commit/98339043d6d8f5b9d601142dcf4b0a764fe9cf79))

## [0.2.0](https://github.com/cveld/terraform-importer/compare/v0.1.3...v0.2.0) (2026-06-02)


### Features

* interactive import flow with live Azure CLI resolution ([f811ce5](https://github.com/cveld/terraform-importer/commit/f811ce597433bcb085ce84ef8ce513df23257303))

## [0.1.3](https://github.com/cveld/terraform-importer/compare/v0.1.2...v0.1.3) (2026-06-02)


### Bug Fixes

* align script name with PyPI package name ([31ef749](https://github.com/cveld/terraform-importer/commit/31ef749785199b9b287df3dd9a3d9c66bb08d20b))

## [0.1.2](https://github.com/cveld/terraform-importer/compare/v0.1.1...v0.1.2) (2026-06-02)


### Bug Fixes

* add PyPI long description and repository URL ([95e0f46](https://github.com/cveld/terraform-importer/commit/95e0f463a3b3e3510e101f66128776b5c1577d5d))
* add workflow_call trigger to publish workflow ([c84f2d2](https://github.com/cveld/terraform-importer/commit/c84f2d2bf4f9c26f01dce6a93e15841d14157179))

## [0.1.1](https://github.com/cveld/terraform-importer/compare/v0.1.0...v0.1.1) (2026-06-02)


### Bug Fixes

* restructure as package to resolve PyPI publish failure ([90d638d](https://github.com/cveld/terraform-importer/commit/90d638d8577931977e5c6409dd09f390a5e48914))

## 0.1.0 (2026-06-02)


### Features

* generate terraform import blocks from binary plan file ([4485a23](https://github.com/cveld/terraform-importer/commit/4485a2382c1def8ae9912ffdc9e9adb6f70e536a))
