(IngeTrazo CAM - GRBL)
(Job: Inch outside)
(Units: in)
(Stock: 4 x 3 x 0.75 in)
(Work zero: front-left corner, stock bottom)
(Safe height: 0.4 in)
(Check the toolpath with an air cut before the first real cut)
(Load T1 [1/4 in Flat End Mill], set Z zero, then run this file)
G90 G94 G17
G20
G54
(Operation: Outside)
(Outside profile)
(Tool T1: 1/4 in Flat End Mill)
S16000 M3
G4 P3
G0 Z1.15
G0 X0.375 Y0.375
G1 Z0.65 F12
G1 Y2.625 F40
G1 X1.3
G1 Z0.67 F12
G1 X1.55 F40
G1 Z0.65 F12
G1 X3.625 F40
G1 Y0.375
G1 X0.375
G0 Z1.15
G1 Z0.55 F12
G1 Y2.625 F40
G1 X1.3
G1 Z0.67 F12
G1 X1.55 F40
G1 Z0.55 F12
G1 X3.625 F40
G1 Y0.375
G1 X0.375
G0 Z1.15
G1 Z0.5 F12
G1 Y2.625 F40
G1 X1.3
G1 Z0.67 F12
G1 X1.55 F40
G1 Z0.5 F12
G1 X3.625 F40
G1 Y0.375
G1 X0.375
G0 Z1.15
M5
M30
