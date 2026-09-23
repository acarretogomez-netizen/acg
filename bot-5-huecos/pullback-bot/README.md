# Bot "5 huecos" · Trading 212

Divide tu saldo en 5 partes y compra acciones grandes europeas (en EUR, sin comisión de cambio) que acaban de caer fuerte **sin haber perdido su tendencia alcista**. Revisa la cartera cada 30 minutos con precios en tiempo real y vende cada posición en cuanto rebota. Funciona solo en GitHub, así que tu ordenador puede estar apagado, y te avisa al móvil.

## La estrategia
- **Universo:** 22 grandes de la zona euro (SAP, ASML, LVMH, Iberdrola, Inditex, Santander, BBVA…).
- **5 huecos:** con 10 € son 2 € por operación, con acciones fraccionadas.
- **Compra** entre las 16:40 y las 17:25: acciones que están por encima de su media de 200 sesiones y tienen un RSI(2) por debajo de 30. Empieza por las más castigadas.
- **Vende** en cualquier momento de la sesión, en cuanto el precio supera su media de 5 sesiones (casi siempre 1-3 días después). También vende con un −10 % (stop) o a los 10 días.

**Backtest con datos reales de 1 hora, de agosto de 2024 a septiembre de 2026** (530 sesiones; los datos empiezan en noviembre de 2023, pero la media de 200 sesiones necesita ese margen para calcularse):

| | Coste 0,05 %/op. | Coste 0,10 %/op. |
|---|---|---|
| 10 € se convierten en | 15,00 € | 13,20 € |
| Comprar y mantener las 22 | 14,19 € | 14,19 € |
| Operaciones | 649 (≈1,2 al día) | 649 |
| Ganadoras | 72 % | 70 % |
| Ganancia media por operación | +0,34 % | +0,24 % |
| Peor caída | −15 % | −16 % |

## Qué te llega al móvil
- 🛒 **COMPRADA Iberdrola**: 0.16 acc. a 12.41 € (≈1.99 €) · RSI2 18
- 💸 **VENDIDA Iberdrola +0.03 €**: 0.16 acc. a 12.62 € · rebote · 1 día dentro
- 📊 **Cartera 10.12 €** (cada hora, de 9:00 a 17:00): cómo va cada posición y tu liquidez
- 🚨 **Error en el bot** si algo falla

## Puesta en marcha
Los pasos están en el chat. En resumen: ntfy en el móvil → clave de la API de Trading 212 → repositorio privado en GitHub con 3 secrets → Actions → `resolve` → `run` con *force*.

## Ajustes (Settings → Secrets and variables → Actions → Variables)
- `DRY_RUN` = `1` → calcula y te avisa de lo que haría, sin mandar órdenes.
- `NOTIFY_PULSE` = `0` → quita el resumen horario.
- `T212_ENV` = `demo` → usa la cuenta de práctica (con una clave de API de demo).
- Para pararlo: *Actions → bot → ··· → Disable workflow*. Las posiciones abiertas se quedan en Trading 212 y puedes venderlas desde la app.

## Seguridad
- Solo vende lo que compró el propio bot (lo comprueba en el historial de órdenes). Lo que compres tú a mano no lo toca.
- Nunca repite una orden a ciegas, porque la API de Trading 212 no protege contra duplicados. Además, dos ejecuciones nunca corren a la vez.
- No opera en festivos ni fuera del horario de 9:05 a 17:25.

## Lo que conviene saber
- En estos 2 años, el bot ha quedado **más o menos a la par** que comprar y mantener. No es una máquina de hacer dinero. La mayor parte del resultado viene de que la bolsa ha subido.
- Estrategias probadas y **descartadas** con los mismos datos, porque perdían dinero después de costes: reversión y momentum cada 5 min y cada hora, estacionalidad intradía, apostar contra los huecos de apertura y comprar al perdedor del día.
- Los datos de Yahoo de las bolsas europeas llegan con 15 min de retraso. Para vender, el bot usa el precio en tiempo real de Trading 212. Para comprar, la señal es diaria y el retraso apenas importa.
- Hacienda: las ganancias y pérdidas van a la renta como ganancias patrimoniales. Trading 212 te da el informe anual.

## Probar en local (opcional)
```bash
pip install -r requirements.txt pytest
pytest -q
export T212_API_KEY=... T212_API_SECRET=... NTFY_TOPIC=...
python bot.py backtest
python bot.py run --force
```
