# CAM glossary (en / es / pt-BR)

This file fixes the vocabulary before any catalogue entry is written, so the
same concept always gets the same word in the UI, the verification
messages and the G-code comments. **A native-speaking machinist should
review it** before release, especially for regional variants: Spain vs
Latin America for Spanish, and Brazil for Portuguese. Change a term here
first, then in `es.json` and `pt-BR.json`.

| en | es | pt-BR | Notes |
|---|---|---|---|
| CAM | CAM | CAM | Not translated |
| Toolpath | Trayectoria de herramienta | Percurso da ferramenta | The UI uses the short form "Trayectorias" / "Percursos" |
| Stock | Material en bruto | Material bruto | "Tablero" / "Chapa" appear only in examples |
| Job | Trabajo | Trabalho | |
| Operation | Operación | Operação | |
| Outside profile | Perfilado exterior | Perfil externo | |
| Inside profile | Perfilado interior | Perfil interno | |
| Pocket | Vaciado | Rebaixo | Also "cajeado" and "bolsão"; the catalogues use only these two |
| Drilling | Taladrado | Furação | |
| Peck (drilling) | Picoteo | Pica-pau | |
| Engraving | Grabado | Gravação | |
| Facing | Planeado | Faceamento | |
| Tab | Puente | Ponte | Holding tab on a cut-out |
| Step-down | Profundidad por pasada | Profundidade por passada | |
| Stepover | Paso lateral | Passo lateral | |
| Feed | Avance | Avanço | |
| Plunge feed | Avance de penetración | Avanço de mergulho | |
| Spindle speed | Velocidad del husillo | Rotação do fuso | |
| Climb | En concordancia | Concordante | |
| Conventional | En oposición | Discordante | |
| Safe height | Altura de seguridad | Altura de segurança | |
| Clearance height | Altura de aproximación | Altura de aproximação | For drilling |
| Work zero / origin | Cero pieza | Zero peça | "Origen de pieza" / "Origem da peça" in explanations |
| Machine bed | Mesa de la máquina | Mesa da máquina | |
| Stock top | Cara superior del material | Face superior do material | |
| Tool | Herramienta | Ferramenta | |
| End mill (flat) | Fresa plana | Fresa de topo reto | |
| Ball end mill | Fresa esférica | Fresa esférica | |
| Bull-nose end mill | Fresa tórica | Fresa toroidal | |
| Chamfer mill | Fresa de chaflán | Fresa de chanfro | |
| Drill | Broca | Broca | |
| Spot drill | Broca de centrar | Broca de centro | |
| Flute length | Longitud de corte | Comprimento de corte | |
| Flutes | Filos | Arestas de corte | |
| Stock to leave (allowance) | Sobrematerial | Sobremetal | |
| Finishing pass | Pasada de acabado | Passe de acabamento | |
| Roughing | Desbaste | Desbaste | |
| Lead-in / lead-out | Entrada / salida tangencial | Entrada / saída tangencial | |
| Ramp / Helix / Plunge | Rampa / Hélice / Penetración vertical | Rampa / Hélice / Mergulho vertical | |
| Tool radius offset | Compensación del radio | Compensação do raio | G41/G42 |
| Rapid (move) | Movimiento rápido | Movimento rápido | |
| Air cut | Corte en vacío | Corte no ar | |
| Gouge | Corte en la pieza (rebaje indebido) | Corte na peça (penetração indevida) | Verifier message |
| Post-processor | Postprocesador | Pós-processador | |
| Bore (milled round hole) | Mandrinado | Mandrilamento | Circular interpolation, not a drill |
| Slot | Ranura | Rasgo | |
| Chamfer | Chaflán | Chanfro | |
| Open pocket | Vaciado abierto | Rebaixo aberto | A pocket reaching the board's edge |
| Open edge | Borde abierto | Borda aberta | |
| V-bit | Fresa en V | Fresa em V | |
| Included angle | Ángulo de la punta | Ângulo da ponta | |
| Set Z zero | Poner el cero en Z | Zerar o eixo Z | |
