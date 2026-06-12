# WORKFLOW of PIPETTE LIQUID HANDLER

### Labeling
- C.M. : Cartesian Module
- A.P. : Automated Pipette
- P.S. : Precision Scaler
- T.B. : Trash Bin for used pipette tips (Need T.B. coordinate)
- T.S. : Tip Storage for new tip reloading (Need T.S. coordinate and interval length between Tips)
- B1. : First Bial filled with Blue Liquid (Need B1. coordinate)
- B2. : Second Bial filled with brown Liquid (Need B2. coordinate)
- B31. : First Target Bial (Need B31. coordinate)
- B32. : Second Target Bial (Need B32. coordinate)

## Setting
- Just once after starting
1. C.M.
   - Move to T.B.
2. A.P.
   - Throw out Tip
   - Turn on A.P. Motor
3. C.M.
   - Move to T.S.
   - Reload new Tip

## Closed-Loop Operation
- Movement of C.M. must be separated into X-direction and Z-direction
- Right before and right after Aspiration, Dispensing, Throwing out Tip and Reloading Tip of A.P., the last C.M. movement should be Z-direction
- B31. and B32. are on the P.S.
1. C.M.
   - Move to B1.
2. A.P.
   - Aspirate 300uL Blue Liquid
3. C.M.
   - Move to B31.
4. A.P. and P.S.
   - Dispense 100uL Blue Liquid
   - Weighing with P.S.
5. C.M.
   - Move to B32. 
6. A.P. and P.S.
   - Dispense 200uL Blue Liquid
   - Weighing with P.S.
7. C.M.
   - Move to T.B.
8. A.P.
   - Throw out Tip
9. C.M.
   - Move to T.S.
   - Reload new Tip

- Go Back to Step1. and operate with B2. and Dispense 200uL Brown Liquid in B31. and 100uL Brown Liquid in B32.

## Ending
1. A.P.
   - Turn off A.P. Motor

