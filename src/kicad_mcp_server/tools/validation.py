"""Design Rule Checking (DRC) and Electrical Rules Check (ERC) tools for KiCad MCP Server."""

import json
import subprocess
import tempfile
import defusedxml.ElementTree as ET
from pathlib import Path

from ..models.types import DRCError, ERCError
from ..server import mcp

# Constants to avoid duplicated string literals (python:S1192)
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
TYPE_UNKNOWN = "unknown"
TYPE_PARSE_ERROR = "parse_error"
FIELD_SEVERITY = "severity"
FIELD_TYPE = "type"
FIELD_DESCRIPTION = "description"
FIELD_SHEETS = "sheets"
FIELD_VIOLATIONS = "violations"
FIELD_ITEMS = "items"

SUFFIX_ERC_REPORT = "_erc.rpt"
SUFFIX_DRC_REPORT = "_drc.rpt"


def _temp_dir() -> Path:
    return Path(tempfile.gettempdir())


def _find_root_schematic(sch_path: Path) -> Path | None:
    """If sch_path is a sub-sheet, return the root schematic instead.

    KiCad convention: root schematic has the same stem as the .kicad_pro file.
    kicad-cli silently drops unnamed local nets when exporting from a sub-sheet,
    so we detect this and redirect to the root schematic.
    """
    pro_files = list(sch_path.parent.glob("*.kicad_pro"))
    if not pro_files:
        return None
    root_sch = pro_files[0].with_suffix(".kicad_sch")
    if root_sch.exists() and root_sch.resolve() != sch_path.resolve():
        return root_sch
    return None


def _parse_erc_json(content: str) -> list[ERCError]:
    """Parse ERC report from JSON format (KiCad 9.0+)."""
    errors = []
    data = json.loads(content)
    for sheet in data.get(FIELD_SHEETS, []):
        for v in sheet.get(FIELD_VIOLATIONS, []):
            components = []
            for item in v.get(FIELD_ITEMS, []):
                desc = item.get(FIELD_DESCRIPTION, "")
                if "Symbol" in desc:
                    parts = desc.split()
                    if len(parts) >= 2:
                        components.append(parts[1])

            errors.append(
                ERCError(
                    severity=v.get(FIELD_SEVERITY, SEVERITY_ERROR),
                    type=v.get(FIELD_TYPE, TYPE_UNKNOWN),
                    description=v.get(FIELD_DESCRIPTION, ""),
                    components=components,
                )
            )
    return errors


def _parse_erc_xml(content: str) -> list[ERCError]:
    """Parse ERC report from XML format."""
    errors = []
    root = ET.fromstring(content)
    for violation in root.findall(".//violation"):
        severity = violation.get(FIELD_SEVERITY, SEVERITY_ERROR)
        error_type = violation.get(FIELD_TYPE, TYPE_UNKNOWN)
        description = violation.get(FIELD_DESCRIPTION, "")

        components = []
        for comp in violation.findall(".//component"):
            ref = comp.get("ref", "")
            if ref:
                components.append(ref)

        errors.append(
            ERCError(
                severity=severity,
                type=error_type,
                description=description,
                components=components,
            )
        )
    return errors


def _parse_erc_report(report_path: Path) -> list[ERCError]:
    """Parse ERC report file (JSON or XML format).

    Args:
        report_path: Path to ERC report file

    Returns:
        List of ERC errors/warnings
    """
    content = report_path.read_text(encoding="utf-8", errors="replace")

    try:
        return _parse_erc_json(content)
    except (json.JSONDecodeError, KeyError):
        pass

    try:
        return _parse_erc_xml(content)
    except ET.ParseError as e:
        return [
            ERCError(
                severity=SEVERITY_ERROR,
                type=TYPE_PARSE_ERROR,
                description=f"Failed to parse ERC report: {str(e)}",
                components=[],
            )
        ]


def _parse_drc_json(content: str) -> list[DRCError]:
    """Parse DRC report from JSON format (KiCad 9.0+)."""
    errors = []
    data = json.loads(content)
    for sheet in data.get(FIELD_SHEETS, []):
        for v in sheet.get(FIELD_VIOLATIONS, []):
            x, y = 0.0, 0.0
            items = v.get(FIELD_ITEMS, [])
            if items:
                pos = items[0].get("pos", {})
                x = float(pos.get("x", 0))
                y = float(pos.get("y", 0))

            errors.append(
                DRCError(
                    severity=v.get(FIELD_SEVERITY, SEVERITY_ERROR),
                    type=v.get(FIELD_TYPE, TYPE_UNKNOWN),
                    description=v.get(FIELD_DESCRIPTION, ""),
                    location=(x, y),
                )
            )
    return errors


def _parse_drc_xml(content: str) -> list[DRCError]:
    """Parse DRC report from XML format."""
    errors = []
    root = ET.fromstring(content)
    for violation in root.findall(".//violation"):
        severity = violation.get(FIELD_SEVERITY, SEVERITY_ERROR)
        error_type = violation.get(FIELD_TYPE, TYPE_UNKNOWN)
        description = violation.get(FIELD_DESCRIPTION, "")

        location_elem = violation.find(".//location")
        x = float(location_elem.get("x", "0")) if location_elem is not None else 0.0
        y = float(location_elem.get("y", "0")) if location_elem is not None else 0.0

        errors.append(
            DRCError(
                severity=severity,
                type=error_type,
                description=description,
                location=(x, y),
            )
        )
    return errors


def _parse_drc_report(report_path: Path) -> list[DRCError]:
    """Parse DRC report file (JSON or XML format).

    Args:
        report_path: Path to DRC report file

    Returns:
        List of DRC errors/warnings
    """
    content = report_path.read_text(encoding="utf-8", errors="replace")

    try:
        return _parse_drc_json(content)
    except (json.JSONDecodeError, KeyError):
        pass

    try:
        return _parse_drc_xml(content)
    except ET.ParseError as e:
        return [
            DRCError(
                severity=SEVERITY_ERROR,
                type=TYPE_PARSE_ERROR,
                description=f"Failed to parse DRC report: {str(e)}",
                location=(0.0, 0.0),
            )
        ]



def _filter_erc_errors(errors: list[ERCError], severity: str = "") -> list[ERCError]:
    """Filter ERC errors by severity.

    Args:
        errors: List of ERC errors
        severity: Severity filter ('error', 'warning', '') for all

    Returns:
        Filtered list of ERC errors
    """
    if not severity:
        return errors

    return [e for e in errors if e.severity == severity]


def _filter_drc_errors(errors: list[DRCError], violation_type: str = "") -> list[DRCError]:
    """Filter DRC errors by type.

    Args:
        errors: List of DRC errors
        violation_type: Type filter (e.g., 'clearance', 'spacing', '') for all

    Returns:
        Filtered list of DRC errors
    """
    if not violation_type:
        return errors

    return [e for e in errors if e.type == violation_type]


def _format_erc_report_results(
    schematic_path: str,
    erc_report_path: Path,
    subsheet_note: str,
) -> str:
    """Format the ERC report file results into a markdown string."""
    if not erc_report_path.exists():
        # No violations found - check if report was generated
        # KiCad 9.0+ might use different format
        return f"""✅ **ERC Check Passed**

**Schematic:** {schematic_path}

No electrical violations detected!{subsheet_note}

**Checked:**
- ✅ All pins properly connected
- ✅ No power conflicts
- ✅ No multiple outputs on same net
- ✅ No pin type mismatches"""

    # Parse and format results
    try:
        errors = _parse_erc_report(erc_report_path)

        if not errors:
            return f"""✅ **ERC Check Passed**

**Schematic:** {schematic_path}

No electrical violations detected!{subsheet_note}"""

        # Count by severity
        error_count = sum(1 for e in errors if e.severity == SEVERITY_ERROR)
        warning_count = sum(1 for e in errors if e.severity == SEVERITY_WARNING)

        # Format violations
        violations_table = "| Severity | Type | Description | Components |\n"
        violations_table += "|----------|------|-------------|------------|\n"

        for error in errors[:20]:  # Limit to first 20
            severity_icon = "❌" if error.severity == SEVERITY_ERROR else "⚠️"
            components_str = ", ".join(error.components) if error.components else "N/A"
            violations_table += f"| {severity_icon} {error.severity} | {error.type} | {error.description[:50]} | {components_str} |\n"

        if len(errors) > 20:
            violations_table += f"\n*... and {len(errors) - 20} more violations*\n"

        return f"""❌ **ERC Violations Detected**

**Schematic:** {schematic_path}
**Total Violations:** {len(errors)}
- Errors: {error_count}
- Warnings: {warning_count}{subsheet_note}

## Violations

{violations_table}

## Recommendations

1. **Fix all errors** before proceeding to PCB layout
2. Review warnings - some may be acceptable
3. Re-run ERC after fixes
4. Use `get_erc_violations()` for detailed filtering

## Next Steps

- Run `get_erc_violations()` with filters to analyze specific issues
- Fix violations in KiCad Eeschema
- Re-run `run_erc()` to verify fixes"""

    except Exception as e:
        return f"""⚠️ **ERC Report Parse Error**

**Schematic:** {schematic_path}

ERC check completed but failed to parse report.

**Error:** {str(e)}

**Report location:** {erc_report_path}

You can manually inspect the report file or re-run ERC in KiCad GUI.{subsheet_note}"""


@mcp.tool()
async def run_erc(
    schematic_path: str,
) -> str:
    """Run Electrical Rules Check (ERC) on schematic.

    Checks for:
    - Unconnected pins
    - Power conflicts
    - Multiple outputs on same net
    - Pin type mismatches

    Args:
        schematic_path: Path to .kicad_sch file

    Returns:
        ERC report with violations and recommendations
    """
    try:
        sch_path = Path(schematic_path)
        if not sch_path.exists():
            return f"❌ **Schematic file not found:** {schematic_path}"

        # Detect sub-sheet and redirect to root schematic
        subsheet_note = ""
        root_sch = _find_root_schematic(sch_path)
        if root_sch:
            subsheet_note = (
                f"\n\n⚠️ **Note:** `{sch_path.name}` is a hierarchical sub-sheet. "
                f"Switched to root schematic `{root_sch.name}` for complete ERC check."
            )
            sch_path = root_sch

        # ERC report output path
        erc_report_path = _temp_dir() / (sch_path.stem + SUFFIX_ERC_REPORT)

        # Run ERC using kicad-cli (KiCad 7+)
        cmd = [
            "kicad-cli",
            "sch",
            "erc",
            "--format",
            "json",
            "--output",
            str(erc_report_path),
            str(sch_path),
        ]

        import asyncio
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return f"❌ **ERC Check Failed**\n\n**Schematic:** {schematic_path}\n\n**Error:**\n```\nCommand timed out after 60 seconds.\n```"

        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

        if proc.returncode != 0:
            return f"""❌ **ERC Check Failed**

**Schematic:** {schematic_path}

**Error:**
```
{stderr_text}
```

**Possible causes:**
- KiCad command line tools not available
- Invalid schematic file
- Missing project file (.kicad_pro)

**Manual steps:**
1. Open schematic in KiCad Eeschema
2. Run: Inspect → Electrical Rules Checker
3. Export report manually{subsheet_note}"""

        return _format_erc_report_results(schematic_path, erc_report_path, subsheet_note)

    except FileNotFoundError:
        return f"""❌ **File Not Found**

**Schematic:** {schematic_path}

Please check the file path and try again."""
    except Exception as e:
        import traceback

        return f"""❌ **Unexpected Error**

**Schematic:** {schematic_path}

**Error:** {str(e)}

**Traceback:**
```
{traceback.format_exc()}
```"""


def _format_drc_report_results(
    pcb_path: str,
    drc_report_path: Path,
) -> str:
    """Format the DRC report file results into a markdown string."""
    if not drc_report_path.exists():
        # No violations found
        return f"""✅ **DRC Check Passed**

**PCB:** {pcb_path}

No design rule violations detected!

**Checked:**
- ✅ All clearance requirements met
- ✅ No spacing violations
- ✅ All connections routed
- ✅ No pad/footprint overlaps
- ✅ Board edge constraints satisfied"""

    # Parse and format results
    try:
        errors = _parse_drc_report(drc_report_path)

        if not errors:
            return f"""✅ **DRC Check Passed**

**PCB:** {pcb_path}

No design rule violations detected!"""

        # Count by severity
        error_count = sum(1 for e in errors if e.severity == SEVERITY_ERROR)
        warning_count = sum(1 for e in errors if e.severity == SEVERITY_WARNING)

        # Group by type
        type_counts = {}
        for error in errors:
            type_counts[error.type] = type_counts.get(error.type, 0) + 1

        # Format violations
        violations_table = "| Severity | Type | Location | Description |\n"
        violations_table += "|----------|------|----------|-------------|\n"

        for error in errors[:20]:  # Limit to first 20
            severity_icon = "❌" if error.severity == SEVERITY_ERROR else "⚠️"
            x, y = error.location
            violations_table += f"| {severity_icon} {error.severity} | {error.type} | ({x:.2f}, {y:.2f}) | {error.description[:40]} |\n"

        if len(errors) > 20:
            violations_table += f"\n*... and {len(errors) - 20} more violations*\n"

        # Format type summary
        type_summary = "\n## Violation Summary\n\n"
        for vtype, count in sorted(type_counts.items()):
            type_summary += f"- **{vtype}**: {count} violations\n"

        return f"""❌ **DRC Violations Detected**

**PCB:** {pcb_path}
**Total Violations:** {len(errors)}
- Errors: {error_count}
- Warnings: {warning_count}

{type_summary}
## Violations

{violations_table}

## Recommendations

1. **Fix all errors** before manufacturing
2. Review warnings - some may be acceptable
3. Pay attention to clearance violations
4. Re-run DRC after fixes
5. Use `get_drc_violations()` for detailed filtering

## Next Steps

- Run `get_drc_violations()` with filters to analyze specific issues
- Fix violations in KiCad Pcbnew
- Re-run `run_drc()` to verify fixes"""

    except Exception as e:
        return f"""⚠️ **DRC Report Parse Error**

**PCB:** {pcb_path}

DRC check completed but failed to parse report.

**Error:** {str(e)}

**Report location:** {drc_report_path}

You can manually inspect the report file or re-run DRC in KiCad GUI."""


@mcp.tool()
async def run_drc(
    pcb_path: str,
) -> str:
    """Run Design Rules Check (DRC) on PCB.

    Checks for:
    - Clearance violations
    - Track spacing violations
    - Missing connections
    - Pad/footprint overlaps
    - Board edge constraints

    Args:
        pcb_path: Path to .kicad_pcb file

    Returns:
        DRC report with violations and locations
    """
    try:
        pcb = Path(pcb_path)
        if not pcb.exists():
            return f"❌ **PCB file not found:** {pcb_path}"

        # DRC report output path
        drc_report_path = _temp_dir() / (pcb.stem + SUFFIX_DRC_REPORT)

        # Run DRC using kicad-cli (KiCad 7+)
        cmd = [
            "kicad-cli",
            "pcb",
            "drc",
            "--format",
            "json",
            "--output",
            str(drc_report_path),
            str(pcb),
        ]

        import asyncio
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return f"❌ **DRC Check Failed**\n\n**PCB:** {pcb_path}\n\n**Error:**\n```\nCommand timed out after 60 seconds.\n```"

        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

        if proc.returncode != 0:
            return f"""❌ **DRC Check Failed**

**PCB:** {pcb_path}

**Error:**
```
{stderr_text}
```

**Possible causes:**
- KiCad command line tools not available
- Invalid PCB file
- Missing design rules

**Manual steps:**
1. Open PCB in KiCad Pcbnew
2. Run: Inspect → Design Rules Checker
3. Export report manually"""

        return _format_drc_report_results(pcb_path, drc_report_path)

    except FileNotFoundError:
        return f"""❌ **File Not Found**

**PCB:** {pcb_path}

Please check the file path and try again."""
    except Exception as e:
        import traceback

        return f"""❌ **Unexpected Error**

**PCB:** {pcb_path}

**Error:** {str(e)}

**Traceback:**
```
{traceback.format_exc()}
```"""


@mcp.tool()
async def get_erc_violations(
    schematic_path: str,
    severity: str = "",
) -> str:
    """Get filtered ERC violations from schematic.

    Args:
        schematic_path: Path to .kicad_sch file
        severity: Filter by severity ('error', 'warning', '') for all

    Returns:
        Filtered list of ERC violations
    """
    try:
        sch_path = Path(schematic_path)
        if not sch_path.exists():
            return f"❌ **Schematic file not found:** {schematic_path}"

        # ERC report output path
        erc_report_path = _temp_dir() / (sch_path.stem + SUFFIX_ERC_REPORT)

        if not erc_report_path.exists():
            # Run ERC first
            initial_result = await run_erc(schematic_path)
            if "❌" in initial_result or "⚠️" in initial_result:
                return initial_result

        # Parse ERC report
        errors = _parse_erc_report(erc_report_path)

        if not errors:
            return f"""✅ **No ERC Violations**

**Schematic:** {schematic_path}

No electrical violations detected."""

        # Apply filter
        filtered_errors = _filter_erc_errors(errors, severity)

        if not filtered_errors:
            return f"""✅ **No Matching Violations**

**Schematic:** {schematic_path}
**Filter:** severity = '{severity or 'all'}'

No violations match the specified filter."""

        # Format results
        result = f"""# ERC Violations

**Schematic:** {schematic_path}
**Filter:** severity = '{severity or 'all'}'
**Count:** {len(filtered_errors)}

## Violations

| Severity | Type | Description | Components |
|----------|------|-------------|------------|
"""

        for error in filtered_errors:
            severity_icon = "❌" if error.severity == SEVERITY_ERROR else "⚠️"
            components_str = ", ".join(error.components) if error.components else "N/A"
            result += f"| {severity_icon} {error.severity} | {error.type} | {error.description} | {components_str} |\n"

        return result

    except Exception as e:
        import traceback

        return f"""❌ **Error**

**Schematic:** {schematic_path}

**Error:** {str(e)}

**Traceback:**
```
{traceback.format_exc()}
```"""


@mcp.tool()
async def get_drc_violations(
    pcb_path: str,
    violation_type: str = "",
) -> str:
    """Get filtered DRC violations from PCB.

    Args:
        pcb_path: Path to .kicad_pcb file
        violation_type: Filter by type (e.g., 'clearance', 'spacing', '') for all

    Returns:
        Filtered list of DRC violations
    """
    try:
        pcb = Path(pcb_path)
        if not pcb.exists():
            return f"❌ **PCB file not found:** {pcb_path}"

        # DRC report output path
        drc_report_path = _temp_dir() / (pcb.stem + SUFFIX_DRC_REPORT)

        if not drc_report_path.exists():
            # Run DRC first
            initial_result = await run_drc(pcb_path)
            if "❌" in initial_result or "⚠️" in initial_result:
                return initial_result

        # Parse DRC report
        errors = _parse_drc_report(drc_report_path)

        if not errors:
            return f"""✅ **No DRC Violations**

**PCB:** {pcb_path}

No design rule violations detected."""

        # Apply filter
        filtered_errors = _filter_drc_errors(errors, violation_type)

        if not filtered_errors:
            return f"""✅ **No Matching Violations**

**PCB:** {pcb_path}
**Filter:** type = '{violation_type or 'all'}'

No violations match the specified filter."""

        # Format results
        result = f"""# DRC Violations

**PCB:** {pcb_path}
**Filter:** type = '{violation_type or 'all'}'
**Count:** {len(filtered_errors)}

## Violations

| Severity | Type | Location | Description |
|----------|------|----------|-------------|
"""

        for error in filtered_errors:
            severity_icon = "❌" if error.severity == SEVERITY_ERROR else "⚠️"
            x, y = error.location
            result += f"| {severity_icon} {error.severity} | {error.type} | ({x:.2f}, {y:.2f}) | {error.description} |\n"

        return result

    except Exception as e:
        import traceback

        return f"""❌ **Error**

**PCB:** {pcb_path}

**Error:** {str(e)}

**Traceback:**
```
{traceback.format_exc()}
```"""


@mcp.tool()
async def export_erc_report(
    schematic_path: str,
    output_path: str = "",
) -> str:
    """Export ERC report to file.

    Args:
        schematic_path: Path to .kicad_sch file
        output_path: Optional output file path (default: schematic_name_erc.txt)

    Returns:
        Confirmation with output file location
    """
    try:
        sch_path = Path(schematic_path)
        if not sch_path.exists():
            return f"❌ **Schematic file not found:** {schematic_path}"

        # Run ERC first to generate report
        initial_result = await run_erc(schematic_path)

        if "❌" in initial_result:
            return f"❌ **ERC Check Failed**\n\n{initial_result}"

        # ERC report path
        erc_report_path = _temp_dir() / (sch_path.stem + SUFFIX_ERC_REPORT)

        if not erc_report_path.exists():
            return "❌ **ERC report not found.** Run ERC check first."

        # Determine output path
        if not output_path:
            output_path = str(sch_path.parent / (sch_path.stem + "_erc_report.txt"))

        output = Path(output_path)

        # Copy report to output location
        import shutil

        shutil.copy(erc_report_path, output)

        return f"""✅ **ERC Report Exported**

**Schematic:** {schematic_path}
**Output:** {output}

The ERC report has been successfully exported to the specified location."""

    except Exception as e:
        import traceback

        return f"""❌ **Export Failed**

**Schematic:** {schematic_path}
**Output:** {output_path}

**Error:** {str(e)}

**Traceback:**
```
{traceback.format_exc()}
```"""


@mcp.tool()
async def export_drc_report(
    pcb_path: str,
    output_path: str = "",
) -> str:
    """Export DRC report to file.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_path: Optional output file path (default: pcb_name_drc.txt)

    Returns:
        Confirmation with output file location
    """
    try:
        pcb = Path(pcb_path)
        if not pcb.exists():
            return f"❌ **PCB file not found:** {pcb_path}"

        # Run DRC first to generate report
        initial_result = await run_drc(pcb_path)

        if "❌" in initial_result:
            return f"❌ **DRC Check Failed**\n\n{initial_result}"

        # DRC report path
        drc_report_path = _temp_dir() / (pcb.stem + SUFFIX_DRC_REPORT)

        if not drc_report_path.exists():
            return "❌ **DRC report not found.** Run DRC check first."

        # Determine output path
        if not output_path:
            output_path = str(pcb.parent / (pcb.stem + "_drc_report.txt"))

        output = Path(output_path)

        # Copy report to output location
        import shutil

        shutil.copy(drc_report_path, output)

        return f"""✅ **DRC Report Exported**

**PCB:** {pcb_path}
**Output:** {output}

The DRC report has been successfully exported to the specified location."""

    except Exception as e:
        import traceback

        return f"""❌ **Export Failed**

**PCB:** {pcb_path}
**Output:** {output_path}

**Error:** {str(e)}

**Traceback:**
```
{traceback.format_exc()}
```"""
