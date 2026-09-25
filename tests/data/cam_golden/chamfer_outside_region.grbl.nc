(IngeTrazo CAM - GRBL)
(Job: Chamfer region)
(Units: mm)
(Stock: 100 x 75 x 20 mm)
(Work zero: front-left corner, stock bottom)
(Safe height: 10 mm)
(Check the toolpath with an air cut before the first real cut)
(Load T5 [90 deg V-bit], set Z zero, then run this file)
G90 G94 G17
G21
G54
(Operation: Chamfer)
(Chamfer)
(Tool T5: 90 deg V-bit)
S16000 M3
G4 P3
G0 Z30
G0 X10 Y10
G1 Z19 F200
G1 Y65 F600
G1 X90
G1 Y10
G1 X10
G0 Z30
M5
M30
