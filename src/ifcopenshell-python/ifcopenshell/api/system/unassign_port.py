# IfcOpenShell - IFC toolkit and geometry engine
# Copyright (C) 2022 Dion Moult <dion@thinkmoult.com>
#
# This file is part of IfcOpenShell.
#
# IfcOpenShell is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IfcOpenShell is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IfcOpenShell.  If not, see <http://www.gnu.org/licenses/>.

import ifcopenshell
import ifcopenshell.api.owner
import ifcopenshell.util.element
from typing import Any


def unassign_port(
    file: ifcopenshell.file, element: ifcopenshell.entity_instance, port: ifcopenshell.entity_instance
) -> None:
    """Unassigns a port to an element

    Ports are typically always assigned to a distribution element, but in
    some edge cases you may want to unassign the port to create an orphaned
    port for cleaning or patchin purposes.

    :param element: The IfcDistributionElement to unassign the port from.
    :type element: ifcopenshell.entity_instance
    :param port: The IfcDistributionPort you want to unassign.
    :type port: ifcopenshell.entity_instance
    :return: None
    :rtype: None

    Example:

    .. code:: python

        # Create a duct
        duct = ifcopenshell.api.root.create_entity(model,
            ifc_class="IfcDuctSegment", predefined_type="RIGIDSEGMENT")

        # Create 2 ports, one for either end.
        port1 = ifcopenshell.api.system.add_port(model, element=duct)
        port2 = ifcopenshell.api.system.add_port(model, element=duct)

        # Unassign one port for some weird reason.
        ifcopenshell.api.system.unassign_port(model, element=duct, port=port1)
    """
    usecase = Usecase()
    usecase.file = file
    usecase.settings = {
        "element": element,
        "port": port,
    }
    return usecase.execute()


class Usecase:
    file: ifcopenshell.file
    settings: dict[str, Any]

    def execute(self):
        if self.file.schema == "IFC2X3":
            return self.execute_ifc2x3()

        for rel in self.settings["element"].IsNestedBy or []:
            if self.settings["port"] in rel.RelatedObjects:
                if len(rel.RelatedObjects) == 1:
                    history = rel.OwnerHistory
                    self.file.remove(rel)
                    if history:
                        ifcopenshell.util.element.remove_deep2(self.file, history)
                    return
                related_objects = set(rel.RelatedObjects) or set()
                related_objects.remove(self.settings["port"])
                rel.RelatedObjects = list(related_objects)
                ifcopenshell.api.owner.update_owner_history(self.file, **{"element": rel})

    def execute_ifc2x3(self):
        for rel in self.settings["element"].HasPorts or []:
            if rel.RelatingPort == self.settings["port"]:
                history = rel.OwnerHistory
                self.file.remove(rel)
                if history:
                    ifcopenshell.util.element.remove_deep2(self.file, history)
                return
