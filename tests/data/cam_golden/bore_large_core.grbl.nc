(IngeTrazo CAM - GRBL)
(Job: Bore large)
(Units: mm)
(Stock: 100 x 75 x 20 mm)
(Work zero: front-left corner, stock bottom)
(Safe height: 10 mm)
(Check the toolpath with an air cut before the first real cut)
(Load T1 [6.0 mm Flat End Mill], set Z zero, then run this file)
G90 G94 G17
G21
G54
(Operation: Bore)
(Bore)
(Tool T1: 6.0 mm Flat End Mill)
S12000 M3
G4 P3
G0 Z30
G0 X62 Y37.5
G1 Z20 F250
G3 X62 Y37.5 Z18 I-12 J0
G1 X56.75 F800
G3 X56.75 Y37.5 I-6.75 J0
G1 X51.5
G3 X51.5 Y37.5 I-1.5 J0
G1 X62
G3 X62 Y37.5 Z16 I-12 J0 F250
G1 X56.75 F800
G3 X56.75 Y37.5 I-6.75 J0
G1 X51.5
G3 X51.5 Y37.5 I-1.5 J0
G1 X62
G3 X62 Y37.5 I-12 J0
G0 Z30
M5
M30
