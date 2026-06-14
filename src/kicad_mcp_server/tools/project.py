"""KiCad project creation tools for KiCad MCP Server - Updated for KiCad 10.0
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from ..server import mcp


def _get_date_string() -> str:
    """Get current date in ISO format."""
    return datetime.now().strftime("%Y-%m-%d")


@mcp.tool()
async def create_kicad_project(
    project_path: str,
    project_name: str,
    title: str = "",
    company: str = "",
) -> str:
    """Create a complete KiCad 10.0 project with template Edge.Cuts centered on A4 sheet.

    This method generates native KiCad 10.0 project files (*.kicad_pro, *.kicad_sch, *.kicad_pcb)
    from scratch, establishing a 50mm x 50mm Edge.Cuts board outline centered at A4 paper center
    (148.5mm, 105.0mm) according to AGENTS.md rules.

    Args:
        project_path: Directory path for the project
        project_name: Name of the project (without extension)
        title: Optional project title
        company: Optional company name

    Returns:
        Confirmation message with created files
    """
    try:
        path = Path(project_path)
        path.mkdir(parents=True, exist_ok=True)

        date_str = _get_date_string()
        title_text = title or project_name
        root_uuid = str(uuid.uuid4())

        # 1. Create .kicad_pro
        pro_file = path / f"{project_name}.kicad_pro"
        pro_data = {
            "meta": {
                "filename": f"{project_name}.kicad_pro",
                "version": 1
            },
            "sheets": [
                [
                    root_uuid,
                    "Root"
                ]
            ]
        }
        with open(pro_file, 'w', encoding='utf-8') as f:
            json.dump(pro_data, f, indent=2)

        # 2. Create .kicad_sch
        sch_file = path / f"{project_name}.kicad_sch"
        sch_content = f"""(kicad_sch
	(version 20260306)
	(generator "eeschema")
	(generator_version "10.0")
	(uuid "{root_uuid}")
	(paper "A4")
	(title_block
		(title "{title_text}")
		(date "{date_str}")
		(company "{company}")
	)
	(lib_symbols
	)
)
"""
        sch_file.write_text(sch_content, encoding='utf-8')

        # 3. Create .kicad_pcb (Centered Edge.Cuts at X=148.5, Y=105.0)
        pcb_file = path / f"{project_name}.kicad_pcb"
        
        # Center of A4 is 148.5, 105.0
        # 50x50mm rectangle limits:
        # X: 148.5 - 25.0 = 123.5 to 148.5 + 25.0 = 173.5
        # Y: 105.0 - 25.0 = 80.0 to 105.0 + 25.0 = 130.0
        uuid_e1 = str(uuid.uuid4())
        uuid_e2 = str(uuid.uuid4())
        uuid_e3 = str(uuid.uuid4())
        uuid_e4 = str(uuid.uuid4())

        pcb_content = f"""(kicad_pcb
	(version 20260206)
	(generator "pcbnew")
	(generator_version "10.0")
	(general
		(thickness 1.6)
		(legacy_teardrops no)
	)
	(paper "A4")
	(layers
		(0 "F.Cu" signal)
		(2 "B.Cu" signal)
		(9 "F.Adhes" user "F.Adhesive")
		(11 "B.Adhes" user "B.Adhesive")
		(13 "F.Paste" user)
		(15 "B.Paste" user)
		(5 "F.SilkS" user "F.Silkscreen")
		(7 "B.SilkS" user "B.Silkscreen")
		(1 "F.Mask" user)
		(3 "B.Mask" user)
		(17 "Dwgs.User" user "User.Drawings")
		(19 "Cmts.User" user "User.Comments")
		(21 "Eco1.User" user "User.Eco1")
		(23 "Eco2.User" user "User.Eco2")
		(25 "Edge.Cuts" user)
		(27 "Margin" user)
		(31 "F.CrtYd" user "F.Courtyard")
		(29 "B.CrtYd" user "B.Courtyard")
		(35 "F.Fab" user)
		(33 "B.Fab" user)
		(39 "User.1" user)
		(41 "User.2" user)
		(43 "User.3" user)
		(45 "User.4" user)
	)
	(setup
		(pad_to_mask_clearance 0)
		(allow_soldermask_bridges_in_footprints no)
		(tenting
			(front yes)
			(back yes)
		)
		(covering
			(front no)
			(back no)
		)
		(plugging
			(front no)
			(back no)
		)
		(capping no)
		(filling no)
	)
	(gr_line
		(start 123.5 80)
		(end 173.5 80)
		(stroke
			(width 0.15)
			(type default)
		)
		(layer "Edge.Cuts")
		(uuid "{uuid_e1}")
	)
	(gr_line
		(start 123.5 130)
		(end 123.5 80)
		(stroke
			(width 0.15)
			(type default)
		)
		(layer "Edge.Cuts")
		(uuid "{uuid_e2}")
	)
	(gr_line
		(start 173.5 80)
		(end 173.5 130)
		(stroke
			(width 0.15)
			(type default)
		)
		(layer "Edge.Cuts")
		(uuid "{uuid_e3}")
	)
	(gr_line
		(start 173.5 130)
		(end 123.5 130)
		(stroke
			(width 0.15)
			(type default)
		)
		(layer "Edge.Cuts")
		(uuid "{uuid_e4}")
	)
)
"""
        pcb_file.write_text(pcb_content, encoding='utf-8')

        return f"""# ✅ KiCad 10.0 Project Created Successfully!

**Project Path:** {path.resolve()}
**Project Name:** {project_name}
**Title:** {title_text}
**Company:** {company}

## 📄 Files Created:

1. **{project_name}.kicad_pro** - KiCad project settings file
2. **{project_name}.kicad_sch** - Schematic template file (KiCad 10.0 Native)
3. **{project_name}.kicad_pcb** - PCB template file (KiCad 10.0 Native)
   - Edge.Cuts (50mm x 50mm) centered at A4 center (148.5mm, 105.0mm) according to AGENTS.md rules.

## 📖 How to Open in KiCad 10.0:

1. Open KiCad 10.0
2. File → Open Project...
3. Navigate to: {pro_file.resolve()}
4. Click Open

Project is ready for KiCad 10.0! 🚀
"""

    except Exception as e:
        import traceback
        return f"Error creating project: {e}\\n\\n{traceback.format_exc()}"
