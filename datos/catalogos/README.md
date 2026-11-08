# Catálogos de referencia

Para comercios que no publican precios en línea (por ejemplo, Tiendas Ara) se puede importar un archivo CSV, JSON o Excel con las columnas de `plantilla.csv`:

| Columna | Obligatoria | Descripción |
|---|---|---|
| `nombre` | sí | Nombre del producto tal como aparece en tienda o folleto |
| `precio` | sí | Precio en pesos (`16300`, `16.300` o `$ 16.300`) |
| `fuente` | sí | De dónde salió el dato: URL del folleto, foto de góndola, visita a tienda (con dirección) |
| `presentacion` | no | Contenido del empaque (`1000 g`, `900 ml`, `30 und`); si falta, la oferta queda como no comparable |
| `marca`, `moneda`, `sku` | no | Datos adicionales |
| `fecha_consulta` | no | Fecha ISO 8601 en que se tomó el precio; si falta, se usa la fecha de modificación del archivo |
| `disponibilidad` | no | `disponible`, `agotado` o vacío |

Los precios importados se marcan en el informe como "catálogo de referencia" y no como consulta en línea. No se incluyó ningún catálogo con precios para no presentar datos inventados como reales.
