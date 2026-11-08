import pytest

from presupuesto_mercado.unidades import cantidad_pedida, convertir_cantidad_usuario, extraer_presentacion, precio_unitario


@pytest.mark.parametrize(
    ("nombre", "contenido", "unidad"),
    [
        ("Arroz DIANA blanco premium (1000  gr)", 1000, "g"),
        ("Aceite Vegetal Imatá 3.000 Ml", 3000, "ml"),
        ("Aceite Premier girasol x900ml", 900, "ml"),
        ("Aceite Canola Life puro x3L", 3000, "ml"),
        ("Aceite Medalla de Oro Mezcla 3 Lt", 3000, "ml"),
        ("Azúcar Manuelita 2.5 kg", 2500, "g"),
        ("Leche Alpina 6 und x 1100 ml", 6600, "ml"),
        ("Leche entera 1100 ml x 6 und", 6600, "ml"),
        ("Aceite GOURMET multiusos x2und (3600 ml)", 3600, "ml"),
        ("Huevo AA rojo x 30 und", 30, "und"),
        ("Papel higiénico Familia 12 rollos", 12, "und"),
        ("Papel higiénico SCOTT jumbo (12 UNIDAD)", 12, "und"),
        ("Arroz Roa 1 lb", 500, "g"),
    ],
)
def test_extrae_presentacion(nombre, contenido, unidad):
    p = extraer_presentacion(nombre)
    assert p is not None
    assert p.contenido == pytest.approx(contenido)
    assert p.unidad_base == unidad


def test_sin_unidad_no_es_interpretable():
    assert extraer_presentacion("Aceite de Coco Virgen Natri 210") is None


def test_conteo_suelto_solo_si_se_permite():
    assert extraer_presentacion("Huevos Kikes x15") is None
    assert extraer_presentacion("Huevos Kikes x15", permitir_conteo_suelto=True).contenido == 15


@pytest.mark.parametrize(
    ("cantidad", "unidad", "valor", "base"),
    [(5, "kg", 5000, "g"), (2, "litros", 2000, "ml"), (3, "libras", 1500, "g"), (1, "docena", 12, "und"),
     (500, "g", 500, "g"), (1, "arroba", 12500, "g"), (2, "bolsas", 2, "empaque")],
)
def test_convierte_unidades_del_usuario(cantidad, unidad, valor, base):
    c = convertir_cantidad_usuario(cantidad, unidad)
    assert (c.valor, c.unidad_base) == (valor, base)


def test_unidad_desconocida():
    assert convertir_cantidad_usuario(2, "cucharadas") is None


def test_empaque_con_presentacion_se_convierte_a_contenido():
    c = cantidad_pedida(1, "paca", "12 rollos")
    assert (c.valor, c.unidad_base) == (12, "und")


def test_precio_unitario_por_kg_y_litro():
    assert precio_unitario(6240, 1000, "g") == (6240, "kg")
    assert precio_unitario(19950, 3000, "ml") == (6650, "L")
