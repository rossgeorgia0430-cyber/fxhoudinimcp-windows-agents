You are building a PDG/TOPs pipeline in Houdini.

Goal: {task_description}

## GOLDEN RULE — USE NATIVE TOP NODES (read this FIRST)

Before writing a Python Processor or Python Script, call list_node_types(context='Top', filter='<keyword>') to check if a dedicated TOP node exists. PDG ships many specialized processors, partitioners, and I/O nodes that handle common tasks without custom code.

## TOP nodes you MUST know (use these instead of custom Python)

| Category | Nodes |
|----------|-------|
| Generators | genericgenerator, filepattern, filerange, rangegenerate, wedge |
| ROP rendering | ropfetch, ropgeometry, ropmantra, ropkarma, ropusd, ropalembic, ropimage, ropfbx, ropflipbook, ropcomposite, ropopengl (drives the `opengl` ROP; H22 viewport is Vulkan, but this TOP/ROP type is still shipped) |
| Processors | hdaprocessor, pythonprocessor, pythonscript |
| Partitioners | partitionbyframe, partitionbyattribute, partitionbyexpression, partitionbyindex, partitionbycombination, partitionbyrange, partitionbytile, waitforall |
| File ops | filepattern, filerange, fileremove, filerename, filecopy, filecompress, filedecompress, makedir |
| Data I/O | csvoutput, csvinput, jsoninput, jsonoutput, sqlinput, xmlinput |
| Attributes | attributecreate, attributecopy, attributedelete, attributerename, attributepromote, attributerandomize, attributefromstring |
| Filtering | filterbyexpression, filterbyattribute, filterbyrange, filterbystate, split |
| Control flow | merge, switch, sort, feedbackbegin, feedbackend, workitemexpand |
| External | ffmpegencodevideo, ffmpegextractimages, imagemagick, downloadfile, urlrequest |
| USD | usdimport, usdimportfiles, usdrender, usdrenderscene |

## Anti-patterns (NEVER do these)

| BAD | USE INSTEAD |
|-----|-------------|
| Python Script to list files | File Pattern TOP |
| Python Script to rename/copy files | File Rename / File Copy TOP |
| Python Script to run a ROP | ROP Fetch TOP |
| Python Script to create wedge variations | Wedge TOP |
| Python Script to partition by frame | Partition by Frame TOP |
| Python Script to encode video from frames | FFmpeg Encode Video TOP |
| Python Script to create CSV | CSV Output TOP |
| No partitioning before merge | Use Wait for All or Partition by Frame to sync |

## Example pipelines (NO custom Python needed)

- **Wedge render:** Wedge → ROP Fetch → Wait for All → FFmpeg Encode Video
- **Batch export:** File Pattern → HDA Processor → ROP Geometry → Wait for All
- **Distributed cache:** Range Generate → ROP Geometry (per frame) → Wait for All → File Copy
- **Post-sim compositing:** File Pattern → Partition by Frame → ROP Composite → FFmpeg Encode Video

## Workflow

1. Navigate to the /tasks context using set_current_network.
2. Plan the dependency graph FIRST — list the TOP nodes you'll use.
3. Create TOP nodes to define the dependency graph.
4. Wire the dependency chain using connect_nodes_batch.
5. Use generate_static_items to preview work items without cooking.
6. Use get_work_item_states to check the pipeline state.
7. Use cook_top_node to execute the pipeline.
8. Monitor with get_work_item_info during cooking.

## Tips

- Use list_node_types with context="Top" to discover available node types
- Check get_top_scheduler_info for scheduler configuration
- Use dirty_work_items to regenerate items after parameter changes
- Wedge TOP is extremely powerful for parameter exploration — use it for variation studies
- ROP Fetch can target any ROP node (Karma, Mantra, Geometry, Alembic, etc.)
- Use Partition by Frame + Wait for All to synchronize before downstream steps
- File Pattern TOP supports glob wildcards — use it to discover existing caches

{network_housekeeping}
