# Task for Kilo (Minimax)

**Context:**
The motor geometry generation using `cadquery` is currently extremely slow. The user wants to speed it up.
The main bottleneck is in `src/motor_ai_sim/cadquery_geometry.py` inside the `_create_coils(self, cq)` method.

Currently, it uses a boolean union operation in a loop to combine all wire objects:
```python
        # Combine all wires in the current slot into a single object for UI performance
            if wires:
                combined_slot_stack = wires[0]
                for w in wires[1:]:
                    if w is not None:
                        combined_slot_stack = combined_slot_stack.union(w)
                coils.append(combined_slot_stack)
```
Boolean `union()` is an exponential `O(N^2)` operation in OpenCASCADE and is killing the CPU performance.

**Your Goal:**
1. Open `src/motor_ai_sim/cadquery_geometry.py`.
2. Find the `_create_coils` method.
3. Remove the heavy `.union(w)` loop.
4. Instead of unioning, combine the individual wire objects into a **CadQuery Assembly or Compound** (or just append them as a list of independent solids if the exporter supports it) so no boolean math is performed.
   - *Hint:* You can use `cq.Compound.makeCompound([w.val() for w in wires if w is not None])` or simply return the flattened list of all individual wire solid objects.
5. Save the file and ensure the geometry generation now completes in < 1 second.

**CRITICAL INSTRUCTION FOR KILO (Server Execution):**
When you need to start the FastAPI server (e.g., `uvicorn`), **do not use blocking commands** like `python src/motor_ai_sim/api.py`.
Instead, use background execution depending on the OS:
- On Windows (PowerShell): `Start-Process -NoNewWindow python -ArgumentList "src/motor_ai_sim/api.py"`
- Or simply run it normally and immediately click/use **"Continue While Running"** in your interface so you don't hang waiting for the server to exit!
Do not wait for the server command to finish; proceed immediately to your next validation steps.