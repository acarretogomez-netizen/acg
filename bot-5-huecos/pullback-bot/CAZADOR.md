# Cazador · acciones en juego (EE. UU.)

Cada día, a las **15:35 (hora de Madrid)**, cinco minutos después de que abra Wall Street, el bot:

1. **Escanea unas 570 acciones de EE. UU.** y se queda con las que están "en juego": abren con un hueco al alza de **+2 % o más**, su primera vela de 5 minutos es alcista y mueven un volumen anormal frente a su media. Casi siempre hay una noticia detrás.
2. **Vigila las 20 con más volumen relativo** y compra (4 huecos de 2,5 €) las que rompen el máximo de sus primeros 5 minutos.
3. **Stop muy ajustado**: 0,1 × su rango medio diario. Las que fallan salen rápido con poca pérdida.
4. **Deja correr las ganadoras** hasta el final y lo vende todo a las **21:55**. Nunca pasa la noche dentro.

Sin apalancamiento, sin cortos, sin deuda: solo compra con tu saldo.

## Backtest con datos reales de 5 minutos (julio a septiembre de 2026, 49 sesiones, 574 acciones)
| | Coste 0,05 %/op. | **Coste 0,20 %/op.** (con el 0,15 % de cambio EUR→USD) |
|---|---|---|
| 10 € se convierten en | 12,50 € | **11,09 €** |
| Operaciones | 159 (≈3,2 al día) | 159 |
| Ganadoras | 21 % | 21 % |
| Pérdida típica (stop) | −1,0 % | −1,0 % |
| Ganancia típica si llega al cierre | +4,9 % | +4,9 % |
| Mejor día / peor día | +11,7 % / −1,2 % | +11,4 % / −1,4 % |

**Así es este perfil:** 4 de cada 5 operaciones pierden un poco, y de vez en cuando una se dispara (en el backtest, Moderna +42 % en un día). Sin esas pocas grandes, el resultado sería plano. Son solo 49 sesiones, así que es una muestra corta.

## Notificaciones
- 🎯 **5 acciones en juego hoy**: TEM +6.1 % vol×7.2 · …
- 🚀 **COMPRADA TEM**: 0.043 acc. a 58.20 $ (≈2.45 €) · stop 57.80 $
- 💸 **VENDIDA TEM −0.03 € (−1.1 %)**: stop, o **VENDIDA TEM +0.18 € (+7.2 %)**: cierre del día
- 🏁 **Fin de la caza · cartera 10.34 €**
