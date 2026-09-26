# CAM en IngeTrazo — guía de uso

**Extensiones ▸ CAM…** convierte un dibujo 2D sobre el material en bruto
en código G para una fresadora o router con **GRBL** o **LinuxCNC**.
Mecaniza en 2,5D: contornos, agujeros y vaciados cortados en capas planas,
en vertical desde la cara superior del material. El torneado con eje
rotativo aún no está disponible.

> **Experimental.** Las trayectorias se verifican antes de exportarlas, pero
> ninguna comprobación sustituye a tus ojos. Ejecuta cada programa nuevo
> primero como **corte en vacío**, con el cero en Z bien por encima del
> material. Comprueba que la máquina va a donde esperas.

## Inicio rápido: un tablero con agujeros

1. Abre **Extensiones ▸ CAM…** y pulsa **Nuevo trabajo CAM…**. Primero se
   nombra el archivo: un trabajo CAM es su propio archivo `.igcam`, no parte
   del modelo. Mientras el trabajo está abierto el modelo queda apartado, y
   vuelve intacto.
2. **Prepara el trabajo** en la pestaña **Trabajo**: el ancho, el fondo, el
   grosor y el material del material en bruto, el **controlador**, las
   **unidades**, los límites de la máquina y el **cero pieza** (el punto que
   tocarás en la máquina: uno de los nueve puntos del material, en su cara
   superior o en su base). Luego pulsa **Empezar el trabajo**. Hasta
   entonces no hay nada más disponible.
3. **Dibuja sobre el material** con las herramientas de dibujo: líneas,
   rectángulos, círculos, arcos, polígonos, equidistancia, mover… La cara
   superior del material es el suelo, visto desde arriba. Las herramientas
   3D (empujar/tirar, sígueme, las de sólidos) están desactivadas en un
   trabajo CAM. Para aprovechar un modelo, selecciona sus caras o su pieza
   antes de abrir CAM y pulsa **Importar contornos del modelo**.
4. Revisa las herramientas en la pestaña **Herramientas**. El diámetro, la
   longitud de corte, los avances y la velocidad del husillo deben ser los
   de tu fresa, no los de ejemplo. **De la biblioteca…** añade una de tus
   fresas con sus avances para este material.
5. En **Operaciones**, elige trazados y pulsa **Añadir operación** (ver
   abajo).
6. Abre **Salida**. El trabajo se calcula solo y las trayectorias aparecen
   sobre el dibujo. La verificación debe decir **No se encontraron
   problemas**.
7. Pulsa **Exportar código G…** y **Guardar** el trabajo (Archivo ▸ Guardar
   también lo guarda). **Volver al modelo** cierra el trabajo.

## Operaciones

La pestaña **Operaciones** muestra los **trazados** del dibujo y sigue
cada cambio: un rectángulo es un trazado cerrado; las líneas que se unen
extremo con extremo son un solo trazado; un agujero redondo es un círculo.
Elige trazados en la lista, o haz clic en cualquiera de sus aristas en el
dibujo (se elige el trazado entero y se dibuja en naranja), y usa **Añadir
operación**. Los trazados cerrados dentro de otro son sus agujeros o
islas.

| Operación | A partir de | Corta |
|---|---|---|
| Perfilado exterior | un trazado cerrado | por fuera: la pieza queda |
| Perfilado interior | un trazado cerrado (un agujero) | por dentro: el agujero sale |
| Vaciado | un trazado cerrado, con los trazados de dentro como islas | vacía toda la zona hasta una profundidad |
| Taladrado | círculos | un agujero por centro, con picoteo si se quiere |
| Grabado | trazados abiertos o cerrados | sigue la propia línea |
| Planeado | nada (todo el material) | aplana la cara superior |
| Vaciado abierto | un trazado cerrado que toca el borde del material | como un vaciado, pero saliendo por sus bordes abiertos: un rebaje, una muesca |
| Mandrinado | círculos | un agujero redondo fresado con movimientos circulares, sin broca |
| Ranura | un rectángulo alargado, o una línea recta | una ranura recta del ancho del rectángulo (o de la herramienta) |
| Chaflán | trazados cerrados o abiertos | un chaflán a 45° (o al ángulo de la fresa) en el borde superior, con fresa en V |

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

**Simular…** (en la cabecera del trabajo, o en **Salida**) abre el trabajo
sobre un bloque de material en 3D y lo reproduce en tiempo de máquina. El
reloj sigue la estimación de tiempo, con los cambios de herramienta y el
arranque del husillo.

- **Reproducción:** volver al inicio, reproducir/pausar, **avanzar** una
  orden, saltar al final o arrastrar la barra a cualquier momento.
  Velocidades de 0,25× a 2000×.
- **El código G** del trabajo corre junto a la vista, con la línea en
  ejecución resaltada. Haz clic en una línea para llevar el trabajo hasta
  ella.
- Bajo la vista: la **fase** (desbaste, acabado, rápido, entrada…), las
  coordenadas **X Y Z** de la punta de la herramienta, la operación, el
  volumen retirado y la herramienta.
- **Calidad:** *Vista previa* mantiene la reproducción fluida; *Alta
  calidad* usa celdas más finas para examinar la superficie terminada. El
  tamaño de celda se muestra al lado. **Simulación final** simula todo el
  trabajo en alta calidad.
- **Abrir código G…** reproduce cualquier programa (de otro CAM, o
  editado a mano) sobre el material de este trabajo; **Programa del
  trabajo** vuelve al suyo.

El material se va retirando al paso de la fresa. Los cortes pasantes se
convierten en agujeros, los puentes quedan en pie y se cuenta el volumen
retirado. En el dibujo, las trayectorias ya cortadas se ven intensas, las
demás se atenúan y la fresa se marca donde está. En un tablero grande, la
malla se hace más gruesa sola.

La simulación muestra lo que hace la trayectoria. No sustituye al corte
en vacío en la máquina.

## La biblioteca de herramientas

La pestaña **Herramientas** tiene las fresas de un trabajo. La **biblioteca
de herramientas** es tu armario de herramientas, que se conserva entre
trabajos, con las velocidades y avances conocidos de cada fresa en cada
material.

- **De la biblioteca…** abre la biblioteca. Elige una fresa y pulsa
  **Añadir al trabajo**: entra en la tabla de herramientas del trabajo con su
  velocidad, avance y penetración **para el material y la máquina del
  trabajo**. El trabajo guarda una copia; cambiar la biblioteca después no
  cambia un trabajo guardado.
- **Guardar en la biblioteca** guarda la herramienta seleccionada: su
  geometría, y su velocidad y avances como *tus propios datos* para este
  material. La próxima vez la biblioteca te devuelve tus valores.

**De dónde salen los valores.** Para cada fresa la biblioteca usa los mejores
datos que tiene, y dice cuáles: tus propios datos, los datos o la tabla de
avances del fabricante, o una **estimación** a partir de una tabla de carga
de viruta. Las estimaciones van marcadas con **≈**; las tablas incluidas son
valores generales, no datos probados. La línea bajo la lista explica cada
ajuste: un plástico que se funde por encima de cierta velocidad, el rango
del husillo, los límites de avance de la máquina. **Comprueba una
estimación con un corte en vacío antes de cortar.** El acero no tiene datos
en la biblioteca: escribe tú sus avances.

Una fresa a la que le faltan medidas (longitud de corte, longitud total…)
aparece como *incompleta* y no puede entrar en un trabajo hasta que las
completes con **Editar…**. Las herramientas eliminadas van a la
**Papelera…**, desde donde se pueden restaurar.

**Catálogos de fabricantes.** **Importar catálogo…** lee la hoja CSV de un
fabricante: Sorotec y CMT (catálogo general y serie 193) vienen incluidos, y
un *perfil de catálogo* (JSON) añade otro fabricante. Cada fila se revisa
antes —un valor en pulgadas en una columna de milímetros, una fresa seis
veces mayor que su mango, un avance imposible— y se muestra como nueva,
cambiada, ya existente o rechazada, con los motivos. No se añade nada hasta
que pulses **Importar**, y **Deshacer la última importación** lo retira. Los
valores que corregiste a mano nunca se sobrescriben en una importación
posterior.

**Archivo de la biblioteca.** La biblioteca es un archivo en tu carpeta de
usuario, con copias de seguridad automáticas (las diez últimas). En un Mac
con 2DCam, **Archivo de la biblioteca… ▸ Compartir la biblioteca de 2DCam**
hace que los dos programas usen la misma.

## Guardar y reutilizar un trabajo

Un trabajo es su propio archivo `.igcam`: la preparación, las herramientas,
el dibujo y las operaciones.

**Plantillas de material.** **Guardar la preparación como plantilla…** en
la pestaña **Trabajo** guarda con un nombre el material en bruto, su tipo,
la máquina, el controlador, las unidades y la tabla de herramientas.
**Nuevo desde una plantilla de material…** en la página de inicio de CAM
empieza un trabajo con una de ellas: junto a las tuyas vienen incluidos
algunos tableros y placas comunes. Una plantilla no tiene dibujo ni
operaciones. **Guardar** (o Archivo ▸ Guardar) lo escribe;
**Trabajos recientes** en la página de inicio de CAM y Archivo ▸ Abrir lo
vuelven a abrir. Todo cambio se puede deshacer.

**Cambia el dibujo y las operaciones lo siguen.** Mueve, estira o desplaza
un trazado y cada operación hecha con él se reconstruye con el trazado
nuevo y se recalcula, conservando todos sus ajustes. Deshacer devuelve
ambos. Una operación cuyo trazado se borró, o cambió tanto que ya no da la
misma operación, se marca con **⚠** y conserva su última geometría hasta
que la borres o la añadas de nuevo.

**Los trazados se revisan mientras dibujas.** Un trazado que se cruza
consigo mismo, o que queda en parte o del todo fuera del material, se
marca con **⚠** en la lista; su descripción emergente dice por qué.

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
