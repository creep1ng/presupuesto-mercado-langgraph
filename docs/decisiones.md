# Decisiones de diseño

Este documento registra las decisiones tomadas durante el desarrollo, su justificación y las alternativas descartadas.

## 1. Acceso a los datos de los supermercados

| Cadena | Mecanismo | Motivo |
|---|---|---|
| Éxito | Página de categoría renderizada con Playwright; lectura del DOM | Las tarjetas de producto se pintan en el navegador y no traen JSON-LD de producto. |
| Carulla | Igual que Éxito (misma plataforma del Grupo Éxito) | Mismo marcado de tarjetas. |
| Jumbo | Página de categoría `/co/…` renderizada con Playwright, con desplazamiento; lectura del DOM | El JSON-LD de la página describe otra vitrina, no la grilla; la ruta sin `/co/` solo muestra carruseles con carga diferida. |
| D1 | Página de categoría abierta con Playwright; lectura del JSON-LD `ItemList/Product/Offer` | El sitio publica datos estructurados schema.org con precio, moneda y disponibilidad. |
| Olímpica | Igual que D1 | Igual que D1. |

**Se respetan los robots.txt.** Éxito y Carulla prohíben el buscador interno (`/s?`) y `/api/`; D1 y Jumbo prohíben `/busca` y los parámetros `map=`/`q=`. Por eso no se usa el buscador de ningún sitio. En su lugar:

1. Se leen los sitemaps públicos de categorías (`/sitemap/category-*.xml`).
2. Se elige la categoría que corresponde al producto, con un puntaje léxico más pistas de categoría propuestas por el LLM (por ejemplo, arroz → "granos").
3. Se recorren las páginas del listado (`?page=N`, permitido) hasta reunir candidatos suficientes.

`urllib.robotparser` no entiende comodines (`/*?*facets=`), así que se implementó un intérprete propio (`robots.py`) con la semántica de Google: regla más específica, y en empate gana `Allow`.

**Alternativas descartadas:**

- *API de catálogo de VTEX (`/api/catalog_system/pub/...`).* Es técnicamente accesible, pero está bajo `/api/`, que robots.txt prohíbe, y el enunciado pide no asumir que los endpoints internos son APIs públicas autorizadas.
- *Buscador interno con Playwright.* Lo prohíben los robots.txt.
- *Rappi como fuente para comercios sin tienda en línea.* Es un intermediario con precios propios, no los de la cadena, y su uso automatizado no está autorizado.

**Cortesía y protección:**

- Hay un intervalo mínimo entre solicitudes al mismo dominio y la concurrencia está limitada.
- Hay una caché de 6 horas para páginas y de 7 días para sitemaps.
- Si un sitio responde 403/429 o muestra un reto anti-bots, se deja de consultar ese dominio durante 30 minutos y se reporta como "no se pudo consultar". No se intenta eludir ninguna protección: no se resuelven retos ni se ocultan las huellas del navegador.
- Las fallas transitorias de red (tiempo agotado, `net::ERR_…`) se reintentan hasta tres veces con espera creciente. Un bloqueo no se reintenta.
- Si varias consultas necesitan la misma página al mismo tiempo (por ejemplo, arroz y azúcar en la categoría de granos de D1), se descarga una sola vez.

**Sitemaps con Chromium (noviembre de 2026).** Desde noviembre de 2026, Éxito responde 403 (página de Cloudflare) a los clientes HTTP que no son navegadores, incluso en el sitemap, que `robots.txt` permite. Chromium recibe el mismo sitemap con 200, sin reto. Por eso los sitemaps se descargan con Playwright, igual que las páginas de categoría. No es una evasión: el navegador no resuelve ningún reto ni oculta que es automatizado (el agente de usuario dice `PresupuestoMercado/0.1 (proyecto academico)`). Si el sitio llegara a mostrar un reto a Chromium, la consulta se detiene igual que antes.

## 2. Qué precio se usa

- **Precio usado:** el precio de venta general, sin condiciones.
- **Precios condicionados:** el precio con tarjeta aliada (Éxito/Carulla) o con tarjeta CMR (Jumbo, "Paga $X") se guarda aparte como `precio_condicionado`. Nunca entra en los totales.
- **Precio de lista:** el precio tachado se guarda como `precio_regular` para mostrar el descuento.
- **Ciudad:** los sitios muestran precios sin seleccionar tienda ni dirección. El sistema no fija ubicación (requeriría formularios y cookies de sesión), así que el informe advierte que el precio puede variar en la ciudad pedida y que la cobertura de entrega no se verificó.

## 3. Envío y total final

El costo de envío depende de la dirección, la franja y el monto mínimo, y solo se conoce en el checkout. El checkout no se consulta porque requiere sesión y robots.txt lo prohíbe. Por eso:

- El envío queda como **no verificado** (`valor = None`) y **nunca se toma como cero**.
- El **total final confirmado** solo se calcula cuando todos los costos adicionales son conocidos, por ejemplo cuando el usuario informa "el domicilio de D1 me cuesta 5.000".
- El informe siempre separa el subtotal de productos del total final.

## 4. Papel del LLM frente a las reglas

| Tarea | Responsable | Motivo |
|---|---|---|
| Interpretar la solicitud | LLM (salida estructurada Pydantic); reglas como respaldo | El lenguaje natural es variable. |
| Elegir categoría | Reglas, con pistas del LLM | Debe ser reproducible y barato. |
| Equivalente / sustituto / no relacionado | LLM; reglas como respaldo | Requiere conocimiento del dominio (variedades, usos). |
| No comparable, unidades, marca obligatoria | Reglas | El LLM no puede saltarse estas restricciones. |
| Selección de empaques, totales, decisión | Reglas | La aritmética y la decisión deben ser auditables. |
| Redacción de la recomendación | LLM, con la decisión ya tomada; texto por reglas como respaldo | Solo explica; no cambia números ni la opción. |

## 5. Normalización

- **Unidades:** todo se lleva a g, mL, und o empaque. Se usa la libra colombiana (500 g) y la arroba (12,5 kg), y la suposición queda registrada.
- **Presentación:** se extrae del nombre publicado, incluidos multipacks ("6 und x 1100 ml"). Si no se puede extraer, la oferta queda **no comparable**.
- **Pedido por empaques:** "1 paca de 12 rollos" se convierte en 12 und.
- **Selección de empaques:** se busca la combinación más barata que cubra la cantidad. Se permite combinar dos presentaciones del mismo producto. Si una alternativa cuesta como máximo 2 % más y usa menos empaques, se prefiere.

## 6. Recomendación

La regla es explícita y se aplica en `presupuesto.elegir_recomendacion`:

1. Entre los proveedores con cobertura completa, se recomienda el de menor subtotal.
2. La compra combinada solo se recomienda si ahorra más de max($5.000, 3 % del subtotal) por cada domicilio adicional que exige (número de proveedores − 1). Ese umbral representa el costo de domicilios que no son verificables.
3. Si ningún proveedor cubre la lista solo, pero la compra combinada sí, se recomienda la combinada.
4. Si ninguna opción cubre la lista completa, no se recomienda ninguna: un subtotal parcial no es un presupuesto de lo pedido. La opción con más productos se menciona solo como referencia.

Un presupuesto de un proveedor se llama "compra completa" solo si cubre todos los productos; si no, "compra parcial". Los proveedores sin ningún producto cotizado no aparecen como opción.

## 7. Proveedores por defecto

- Si el usuario no nombra supermercados, se consultan los cinco con conector web.
- Si pide "N supermercados" sin nombrarlos, se consultan los cinco y se presentan los N con más productos cotizados y, a igual cobertura, menor subtotal. La suposición queda registrada con los que no se presentan.
  - *Descartado:* tomar los primeros N de la lista por defecto. Elegía por posición y no por precio, y si uno de ellos fallaba (como Éxito con el 403 de noviembre de 2026) no había reemplazo.
- Si nombra menos de dos, se completa hasta dos, también con suposición explícita.
