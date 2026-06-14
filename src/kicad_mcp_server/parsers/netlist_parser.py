"""KiCad netlist parser for accurate component network tracking."""

import xml.etree.ElementTree as ET
import defusedxml.ElementTree as DET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NetlistComponent:
    """Component from netlist file."""

    reference: str
    value: str
    library: str
    footprint: str | None = None
    pins: dict[str, str] = field(default_factory=dict)  # pin_number -> net_name


@dataclass
class NetlistNet:
    """Net from netlist file."""

    name: str
    code: int
    pins: list[tuple[str, str]] = field(default_factory=list)  # (reference, pin_number)


class NetlistParser:
    """Parser for KiCad netlist files (.xml)."""

    def __init__(self, file_path: str) -> None:
        """Initialize parser with netlist file.

        Args:
            file_path: Path to .xml netlist file

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file is not a .xml file
        """
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"Netlist file not found: {file_path}")

        if self.file_path.suffix != ".xml":
            raise ValueError(f"Netlist file must be .xml, got: {self.file_path.suffix}")

        self._data: dict[str, Any] | None = None

    def _parse_components(self, root: ET.Element) -> dict[str, NetlistComponent]:
        """Extract components from the root XML element.

        Args:
            root: XML root element

        Returns:
            Dictionary mapping reference to component
        """
        components = {}
        for comp in root.findall(".//comp"):
            ref = comp.get("ref")
            if not ref:
                continue
            value = comp.findtext("value", "")
            library = comp.findtext("libsource/libpart", "")
            footprint = comp.findtext("footprint/libpart", "")

            components[ref] = NetlistComponent(
                reference=ref,
                value=value,
                library=library,
                footprint=footprint,
                pins={},
            )
        return components

    def _parse_single_net(
        self, net: ET.Element, components: dict[str, NetlistComponent]
    ) -> tuple[str, NetlistNet] | None:
        """Parse a single net XML element and populate component pins.

        Args:
            net: XML net element
            components: Dictionary of existing components to populate pins for

        Returns:
            Tuple of (net_name, NetlistNet) or None if validation fails
        """
        code_str = net.get("code")
        name = net.get("name")
        if not code_str or not name:
            return None
        try:
            code = int(code_str)
        except ValueError:
            return None

        pins_list = []
        for node in net.findall("node"):
            ref = node.get("ref")
            pin_num = node.get("pin")
            if ref and pin_num:
                pins_list.append((ref, pin_num))

                if ref in components:
                    components[ref].pins[pin_num] = name

        return name, NetlistNet(name=name, code=code, pins=pins_list)

    def _parse_nets(
        self, root: ET.Element, components: dict[str, NetlistComponent]
    ) -> dict[str, NetlistNet]:
        """Extract nets from the root XML element and populate component pins.

        Args:
            root: XML root element
            components: Dictionary of existing components to populate pins for

        Returns:
            Dictionary mapping net name to net
        """
        nets = {}
        for net in root.findall(".//net"):
            result = self._parse_single_net(net, components)
            if result is not None:
                name, net_obj = result
                nets[name] = net_obj
        return nets

    def _parse_file(self) -> dict[str, Any]:
        """Parse the netlist XML file.

        Returns:
            Parsed data structure
        """
        if self._data is not None:
            return self._data

        tree = DET.parse(self.file_path)
        root = tree.getroot()

        components = self._parse_components(root)
        nets = self._parse_nets(root, components)

        self._data = {
            "components": components,
            "nets": nets,
        }

        return self._data

    def _get_net_name(self, root: ET.Element, net_code: str) -> str:
        """Get net name from net code.

        Args:
            root: XML root element
            net_code: Net code string (e.g., "3")

        Returns:
            Net name
        """
        net_elem = root.find(f".//net[@code='{net_code}']")
        if net_elem is not None:
            return net_elem.get("name", f"net_{net_code}")
        return f"net_{net_code}"

    def get_components(self) -> dict[str, NetlistComponent]:
        """Get all components from netlist.

        Returns:
            Dictionary mapping reference to component
        """
        data = self._parse_file()
        return data["components"]

    def get_nets(self) -> dict[str, NetlistNet]:
        """Get all nets from netlist.

        Returns:
            Dictionary mapping net name to net
        """
        data = self._parse_file()
        return data["nets"]

    def get_component_nets(self, reference: str) -> dict[str, list[str]]:
        """Get all networks for a component.

        Args:
            reference: Component reference (e.g., "R16")

        Returns:
            Dictionary mapping net name to list of connected pins
        """
        components = self.get_components()
        if reference not in components:
            return {}

        comp = components[reference]
        net_pins = {}

        for pin_num, net_name in comp.pins.items():
            if net_name not in net_pins:
                net_pins[net_name] = []
            net_pins[net_name].append(pin_num)

        return net_pins

    def get_net_components(self, net_name: str) -> list[tuple[str, str]]:
        """Get all components connected to a net.

        Args:
            net_name: Net name (e.g., "PMIC_I2C_SCL")

        Returns:
            List of (reference, pin_number) tuples
        """
        nets = self.get_nets()
        if net_name not in nets:
            return []

        return nets[net_name].pins

    def trace_connection(self, reference: str, pin_number: str | None = None) -> dict[str, Any]:
        """Trace connections from a component pin.

        Args:
            reference: Component reference
            pin_number: Optional pin number (if None, trace all pins)

        Returns:
            Connection information
        """
        components = self.get_components()
        if reference not in components:
            return {"error": f"Component {reference} not found in netlist"}

        comp = components[reference]

        if pin_number:
            # Trace specific pin
            if pin_number not in comp.pins:
                return {"error": f"Pin {pin_number} not found in component {reference}"}

            net_name = comp.pins[pin_number]
            nets = self.get_nets()
            connected = nets[net_name].pins if net_name in nets else []

            return {
                "component": reference,
                "pin": pin_number,
                "net": net_name,
                "connected_to": [(ref, pin) for ref, pin in connected if ref != reference],
            }

        else:
            # Trace all pins
            net_connections = {}
            for pin_num, net_name in comp.pins.items():
                nets = self.get_nets()
                connected = nets[net_name].pins if net_name in nets else []
                net_connections[net_name] = {
                    "pin": pin_num,
                    "connected_to": [(ref, pin) for ref, pin in connected if ref != reference],
                }

            return {
                "component": reference,
                "nets": net_connections,
            }
