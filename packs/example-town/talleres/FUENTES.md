# Ilustraciones de los talleres de Sampleton (2026-08-04)

Son las MISMAS ilustraciones que usa la instalación de Pinamar
(the private deployment pack, with origin and credits recorded there:
Icons8 estilo "Anthropic Claude Hand Drawn", más tres generadas con
gpt-image-2), re-tinteadas de la paleta de Pinamar (verde menta sobre blanco)
a la del tema `humanrounds` que sirve la demo pública — papel #f7f3eb, tinta
#181716, acento terracota.

El re-tinteo lo hace `scripts/recolor_illustrations.py` (rotación de matiz en
HSV: conserva saturación, valor y la textura del lápiz). Es determinista, así
que volver a correrlo no produce diff:

    python3 scripts/recolor_illustrations.py \
        --src /path/to/private-town-pack/talleres \
        --out packs/example-town/talleres --theme humanrounds

Los nombres pierden el sufijo de localidad de Pinamar (`-ostende`,
`-valeria`), que en Sampleton no significa nada, y
`fortalecimiento-familiar` pasa a llamarse `estimulacion-temprana` por el
taller que ilustra acá.

| archivo | taller |
|---|---|
| `prevencion-de-caidas.jpg` | Taller de gimnasia para adultos mayores |
| `estimulacion-temprana.jpg` | Taller de estimulación temprana |
| `ansiedad.jpg` | Grupo de ansiedad |
| `estimulacion-cognitiva.jpg` | Taller de estimulación cognitiva |
| `cesacion-tabaquica.jpg` | Cesación tabáquica |
| `acompanamiento-embarazo.jpg` | Curso de acompañamiento del embarazo |
| `adolescentes.jpg` | Espacio para adolescentes |
| `taller-escritura.jpg` | Taller de escritura |
