(IngeTrazo CAM - GRBL)
(Job: Multi)
(Units: mm)
(Stock: 100 x 75 x 20 mm)
(Work zero: front-left corner, stock bottom)
(Safe height: 10 mm)
(Check the toolpath with an air cut before the first real cut)
(Load T3 [5 mm Drill], set Z zero, then run this file)
G90 G94 G17
G21
G54
(Operation: Holes)
(Drilling)
(Tool T3: 5 mm Drill)
S6000 M3
G4 P3
G0 Z30
G0 X20 Y20
G0 Z25
G1 Z10 F150
G0 Z25
G0 X80 Y55 Z30
G0 Z25
G1 Z10
G0 Z25
G0 Z30
M5
M30
