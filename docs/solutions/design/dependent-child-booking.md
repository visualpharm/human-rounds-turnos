# Turnos para hijos y personas menores a cargo

## Decisión

El adulto conserva la cuenta y el contacto. El menor conserva la identidad clínica: **DNI, nombre, apellido, nacimiento y sexo**. Nombre + edad no alcanza: la edad cambia y HSI necesita una persona estable.

Mi Salud Digital Bonaerense permite agregar menores al grupo familiar mediante declaración jurada y actuar en su nombre. Su documentación pública concentra el flujo de representación en menores de 13; Sana admite menores de 18 para no bloquear la gestión familiar local, pero guarda el vínculo y quién declaró los datos.

Fuentes: [Mi Salud Digital Bonaerense](https://www.ms.gba.gov.ar/sitios/misalud/) · [Turnos web bonaerenses](https://www.ms.gba.gov.ar/sitios/saluddigitalbonaerense/turnos-web/) · [WebTurno](https://webturno.com.ar/)

## Datos

- `dependientes`: adulto_dni + DNI del menor + identidad + vínculo + fecha de declaración jurada.
- `solicitudes.dni`: paciente atendido, incluso si es menor.
- `solicitudes.solicitante_dni`: adulto que gestionó el turno.
- La cola, HSI y la comparación de la orden usan la identidad del menor.
- La cuenta, avisos y recuperación siguen usando el contacto del adulto.

## Experiencia

1. Buscar servicios no se filtra por la edad del titular.
2. Después de ingresar o crear la cuenta: “¿Para quién es la atención?”.
3. Elegir “Yo”, un menor guardado o “Agregar un hijo o persona a cargo”.
4. Agregar pide DNI, nombre, apellido, nacimiento, sexo, vínculo y declaración.
5. El gate de edad, la orden, la confirmación, Mis solicitudes y calendario usan el paciente elegido.

Un adulto sin cuenta se registra dentro del mismo wizard y continúa directamente a elegir/agregar al menor; no reinicia el trámite.
