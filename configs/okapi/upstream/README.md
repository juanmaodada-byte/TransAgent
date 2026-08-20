# Okapi 1.48.0 OpenXML Upstream Notes

Official index:

`https://okapiframework.org/binaries/main/1.48.0/`

Planned local runtime package for this Darwin arm64 machine:

`https://okapiframework.org/binaries/main/1.48.0/okapi-apps_cocoa-macosx-aarch64_1.48.0.zip`

Downloaded ZIP SHA-256:

`706558be5e6e6dc55d841189945b15413d645e51f1eabccba2111afeb46baae4`

Tikal entry points:

- Official script: `.runtime/okapi-1.48.0/tikal.sh`
- Local Java 17 wrapper: `.runtime/okapi-1.48.0/tikal-java17.sh`

Okapi/Tikal version output:

`Okapi Tikal - Localization Toolset`, `Version: 2.1.48.0`

Tikal-listed OpenXML configuration ID:

`okf_openxml`

Upstream OpenXML configuration source found in the official app package:

`okapi-lib-1.48.0.jar!/net/sf/okapi/filters/openxml/wordConfiguration.yml`

The extracted copy is saved as:

`configs/okapi/upstream/openxml_1.48.0.fprm`

Important capability boundary:

The Okapi 1.48.0 app package did not publish a DOCX-specific `.fprm` file in
the top-level `config/` directory. The discovered OpenXML defaults are embedded
YAML resources inside `okapi-lib-1.48.0.jar`. The runnable built-in ID is
`okf_openxml`; the custom target `okf_openxml@openxml_docx_p0` is not currently
registered. D2 therefore cannot claim GO for the product P0 config contract.
