# CAM en IngeTrazo — guía de uso

**Extensiones ▸ CAM…** convierte lo que modelas en código G para una
fresadora o router con **GRBL** o **LinuxCNC**. Mecaniza en 2,5D: contornos,
agujeros y vaciados cortados en capas planas, en vertical desde el plano de
mecanizado.

> **Experimental.** Las trayectorias se verifican antes de exportarlas, pero
> ninguna comprobación sustituye a tus ojos. Ejecuta cada programa nuevo
> primero como **corte en vacío**, con el cero en Z bien por encima del
> material. Comprueba que la máquina va a donde esperas.

## Inicio rápido: un tablero con agujeros

1. Modela la pieza como un sólido: un tablero con sus agujeros y vaciados.
   Conviértela en grupo, o separa un componente en piezas.
2. Selecciona la pieza en el modelo o en la bandeja Piezas.
3. Abre **Extensiones ▸ CAM…**. En la pestaña **Trabajo**, pulsa
   **Pieza → operaciones**. CAM añade todas las operaciones que necesita la
   pieza:
   - un **vaciado** por cada agujero ciego, a su profundidad;
   - **taladrado** para cada agujero pasante redondo que coincida con una
     broca de la tabla de herramientas;
   - un **perfilado interior** para los demás agujeros pasantes;
   - un **perfilado exterior** alrededor del contorno, con cuatro puentes.
4. Revisa las herramientas en la pestaña **Herramientas**. El diámetro, la
   longitud de corte, los avances y la velocidad del husillo deben ser los
   de tu fresa, no los de ejemplo.
5. Elige el **controlador**, las **unidades** y el **cero pieza** en la
   pestaña **Trabajo**. El cero pieza es el punto que tocarás en la máquina:
   uno de los nueve puntos del material, en su cara superior o en su base.
6. Abre **Salida**. El trabajo se calcula solo y las trayectorias aparecen
   en el modelo. La verificación debe decir **No se encontraron problemas**.
7. Pulsa **Exportar código G…**.

## Operaciones

Selecciona caras, aristas o una pieza y usa
**Operaciones ▸ Añadir desde la selección**.

| Operación | A partir de | Corta |
|---|---|---|
| Perfilado exterior | una cara, aristas cerradas, el contorno de una pieza | por fuera: la pieza queda |
| Perfilado interior | agujeros de una cara, aristas cerradas | por dentro: el agujero sale |
| Vaciado | una cara con sus agujeros como islas, o aristas cerradas | vacía toda la zona hasta una profundidad |
| Taladrado | agujeros redondos (círculos) | un agujero por centro, con picoteo si se quiere |
| Grabado | aristas abiertas o cerradas | sigue la propia línea |
| Planeado | nada (todo el material) | aplana la cara superior |
| Vaciado abierto | una cara que toca el borde del tablero | como un vaciado, pero saliendo por sus bordes abiertos: un rebaje, una muesca |
| Mandrinado | agujeros redondos | un agujero redondo fresado con movimientos circulares, sin broca |
| Ranura | un rectángulo alargado, o una arista recta | una ranura recta del ancho del rectángulo (o de la herramienta) |
| Chaflán | una cara (su contorno y sus agujeros), aristas | un chaflán a 45° (o al ángulo de la fresa) en el borde superior, con fresa en V |

Cada operación tiene una herramienta, una profundidad y una **profundidad
por pasada**. El resto de ajustes depende de la operación:

- **Dirección.** *En concordancia* deja el material a la derecha de la
  fresa con el husillo a derechas. Suele dar mejor acabado en una máquina
  rígida. *En oposición* es lo contrario.
- **Entrada.** *Penetración vertical* baja recta. *Rampa* baja a lo largo
  del corte y vuelve para limpiar la cuña. *Hélice* baja en espiral, solo
  donde cabe junto a la pieza; si no cabe, usa rampa.
- **Sobrematerial** y **Pasadas de acabado.** Desbasta un poco por fuera
  de la medida y termina con una pasada ligera. Esa pasada puede usar una
  **herramienta de acabado** distinta.
- **Puentes.** Sujetan la pieza recortada al tablero. Indica su número,
  ancho y alto. Córtalos después con una fresa de copiar o un cúter.
- **Entrada y salida tangencial.** Empiezan y terminan el corte a lo largo
  de un borde, no sobre él.
- **Compensación del radio.** *En el programa* es lo habitual. *En el
  controlador* escribe `G41`/`G42` y deja la compensación a la tabla de
  herramientas de la máquina. Solo funciona en LinuxCNC.
- **Taladrado.** El *picoteo* taladra por tramos y saca la viruta entre
  ellos. La *pausa* se detiene en el fondo.
- **Mandrinado.** Cualquier agujero redondo mayor que la fresa: baja en
  espiral una profundidad por pasada por vuelta y termina la pared con un
  círculo plano. Los agujeros anchos se vacían hasta el centro.
  **Pieza → operaciones** usa un mandrinado para los agujeros redondos
  que ninguna broca de la tabla iguala.
- **Ranura.** Desde un rectángulo, la ranura es ese rectángulo, con el
  radio de la fresa en sus esquinas interiores. Desde una arista recta, es
  un canal del ancho de la herramienta, un radio más largo en cada extremo.
- **Chaflán.** Necesita una **fresa en V** (de chaflán) en la tabla de
  herramientas (los trabajos nuevos traen una de 90°). El *ancho* es
  cuánto se quita del borde. La profundidad para ello depende del ángulo:
  con 90°, la profundidad es igual al ancho. Una cara da un chaflán
  alrededor de su contorno y otro dentro de cada agujero; *En el borde de
  un agujero* cambia el lado.
- **Vaciado abierto.** Los bordes que están sobre el contorno del tablero
  se detectan y se marcan **abiertos** solos. La fresa los atraviesa, y
  guarda su radio respecto de los demás bordes. *Bordes abiertos* los
  lista por número; selecciona la operación para ver los números (y los
  bordes abiertos, en verde a trazos) en el modelo.

## Controladores

**GRBL** (GRBL 1.1, grblHAL, FluidNC). GRBL no tiene cambiador de
herramientas, así que un trabajo con varias herramientas se escribe como
**un archivo por cambio de herramienta**, numerado en orden de ejecución
(`tablero_01_T1_….nc`, `tablero_02_T3_….nc`…). Ejecútalos en orden. Entre
uno y otro, cambia la herramienta y **vuelve a poner el cero en Z**, porque
cada herramienta tiene su longitud. Los ciclos de taladrado se escriben
como movimientos simples.

**LinuxCNC** (2.9 o posterior). Un solo archivo `.ngc`. Los cambios de
herramienta usan `T# M6` con `G43 H#`, así que las longitudes salen de la
tabla de herramientas de tu máquina. El taladrado usa ciclos fijos
(`G81`/`G82`/`G83`). La exportación también escribe una **tabla de herramientas** (`.tbl`)
junto al programa, con los números y diámetros de las herramientas del
trabajo: un `T# M6` de una herramienta que falta en la tabla de la
máquina detiene el programa. Las longitudes quedan en 0, para medirlas
en la máquina. La tabla va en las unidades del trabajo, que deben ser
las de la máquina.

Los dos admiten milímetros (`G21`) o pulgadas (`G20`), según las
**Unidades** del trabajo. Los comentarios van en tu idioma, reducidos a
ASCII.

## Simulación

**Salida ▸ Simular…** abre el trabajo sobre un bloque de material en 3D
y lo reproduce en tiempo de máquina. El reloj sigue la estimación de
tiempo, con los cambios de herramienta y el arranque del husillo.
Reproduce, pausa, arrastra la barra a cualquier momento o salta al final.
La velocidad llega a 500×.

El material se va retirando al paso de la fresa. Los cortes pasantes se
convierten en agujeros, los puentes quedan en pie y se cuenta el volumen
retirado. En la ventana del modelo, las trayectorias ya cortadas se ven
intensas, las demás se atenúan y la fresa se marca donde está.
**Resolución** equilibra detalle y velocidad. En un tablero grande, la
malla se hace más gruesa sola.

La simulación muestra lo que hace la trayectoria. No sustituye al corte
en vacío en la máquina.

## El trabajo viaja con el modelo

El trabajo se guarda en el `.igz` y cada cambio se puede deshacer. Si
cambias el modelo después, pulsa **Actualizar desde la pieza** en la
pestaña **Trabajo**. Las operaciones siguen al nuevo contorno y a los
nuevos agujeros, y conservan sus ajustes. Una operación que ya no coincide
conserva su geometría anterior, y CAM la nombra.

## Verificación

No se puede exportar mientras la verificación indique un error:

- una fresa que cortaría en la pieza (un *rebaje indebido*);
- un movimiento rápido dentro del material o a través de él;
- un corte por debajo de la base del material;
- una profundidad mayor que la longitud de corte de la herramienta;
- una velocidad o un avance fuera de los límites de la máquina (se
  ajustan en la pestaña Trabajo).

Cada archivo escrito se vuelve a leer como lo leería el controlador, y
debe reproducir la trayectoria con un error máximo de 0,001 mm.
