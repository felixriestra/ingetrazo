(IngeTrazo CAM - GRBL)
(Job: Slot wide)
(Units: mm)
(Stock: 100 x 75 x 20 mm)
(Work zero: front-left corner, stock bottom)
(Safe height: 10 mm)
(Check the toolpath with an air cut before the first real cut)
(Load T1 [6.0 mm Flat End Mill], set Z zero, then run this file)
G90 G94 G17
G21
G54
(Operation: Slot)
(Slot)
(Tool T1: 6.0 mm Flat End Mill)
S12000 M3
G4 P3
G0 Z30
G0 X20 Y33.5
G1 Z17 F250
G1 X80 F800
G1 Y36.167
G1 X20
G1 Y38.833
G1 X80
G1 Y41.5
G1 X20
G0 Z30
G0 Y33.5
G1 Z14 F250
G1 X80 F800
G1 Y36.167
G1 X20
G1 Y38.833
G1 X80
G1 Y41.5
G1 X20
G0 Z30
M5
M30
