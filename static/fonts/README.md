# Manrope and IBM Plex Mono (theme "humanrounds")

`manrope-variable-<subset>.woff2` (one variable file per subset, weights
400–800) and `ibm-plex-mono-<400|500>-<subset>.woff2` are the exact WOFF2
subsets Google Fonts serves for the theme's families, self-hosted since
2026-09-17 like Ubuntu below; `humanrounds.css` holds their `@font-face`
rules with Google's unicode ranges. Both families are licensed under the SIL
Open Font License 1.1 (https://openfontlicense.org).

# Ubuntu font formats

The `ubuntu-400-latin.ttf` and `ubuntu-700-latin.ttf` files are decompressed
TrueType copies of the same-named WOFF2 subsets already used by the site.
Glyphs, names, metrics and copyright metadata are unchanged. The PDF poster
uses TTF so it does not depend on system fonts or FreeType WOFF2 support.

Embedded copyright: Copyright 2011 Canonical Ltd. Licensed under the Ubuntu
Font Licence 1.0. License: https://ubuntu.com/legal/font-licence

Conversion: load the existing WOFF2 with `fontTools.ttLib.TTFont`, set
`flavor = None`, then save as TTF. FontTools is a development-only conversion
tool, not a runtime dependency.
