# Ifc5D - IFC costing utility
# Copyright (C) 2021 Dion Moult <dion@thinkmoult.com>
#
# This file is part of Ifc5D.
#
# Ifc5D is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ifc5D is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Ifc5D.  If not, see <http://www.gnu.org/licenses/>.

import os
import types
import json
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.pset
import ifcopenshell.geom
import ifcopenshell.util.unit
import ifcopenshell.util.element
import ifcopenshell.util.selector
import ifcopenshell.util.shape
import ifcopenshell.util.representation
import ifcopenshell.util.type
import multiprocessing
from collections import namedtuple, defaultdict
from typing import Any, Literal, get_args, Union, Iterable


Function = namedtuple("Function", ["measure", "name", "description"])
RULE_SET = Literal["IFC4QtoBaseQuantities", "IFC4QtoBaseQuantitiesBlender"]
rules: dict[RULE_SET, dict[str, Any]] = {}
ResultsDict = dict[ifcopenshell.entity_instance, dict[str, dict[str, float]]]
QtosFormulas = dict[str, dict[str, str]]

cwd = os.path.dirname(os.path.realpath(__file__))
for name in get_args(RULE_SET):
    with open(os.path.join(cwd, name + ".json"), "r") as f:
        rules[name] = json.load(f)


def quantify(ifc_file: ifcopenshell.file, elements: set[ifcopenshell.entity_instance], rules: dict) -> ResultsDict:
    """

    :param rules: Set of rules from `ifc5d.qto.rules`.

    """
    results: ResultsDict = {}
    elements_by_classes: defaultdict[str, set[ifcopenshell.entity_instance]] = defaultdict(set)
    for element in elements:
        elements_by_classes[element.is_a()].add(element)

    for calculator, queries in rules["calculators"].items():
        calculator = calculators[calculator]
        for ifc_class, qtos in queries.items():
            filtered_elements = set()
            ifc_classes = [ifc_class] + ifcopenshell.util.type.get_applicable_types(ifc_class, ifc_file.schema)
            for ifc_class in ifc_classes:
                if ifc_class not in elements_by_classes:
                    continue
                filtered_elements.update(elements_by_classes[ifc_class])
            if filtered_elements:
                calculator.calculate(ifc_file, filtered_elements, qtos, results)
    return results


def edit_qtos(ifc_file: ifcopenshell.file, results: ResultsDict) -> None:
    """Apply quantification results as quantity sets."""
    for element, qtos in results.items():
        for name, quantities in qtos.items():
            qto = ifcopenshell.util.element.get_pset(element, name, should_inherit=False)
            if qto:
                qto = ifc_file.by_id(qto["id"])
            else:
                qto = ifcopenshell.api.pset.add_qto(ifc_file, element, name)
            ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto, properties=quantities)


class SI2ProjectUnitConverter:
    def __init__(self, ifc_file: ifcopenshell.file):
        self.project_units = {
            "IfcAreaMeasure": ifcopenshell.util.unit.get_project_unit(ifc_file, "AREAUNIT"),
            "IfcLengthMeasure": ifcopenshell.util.unit.get_project_unit(ifc_file, "LENGTHUNIT"),
            "IfcMassMeasure": ifcopenshell.util.unit.get_project_unit(ifc_file, "MASSUNIT"),
            "IfcTimeMeasure": ifcopenshell.util.unit.get_project_unit(ifc_file, "TIMEUNIT"),
            "IfcVolumeMeasure": ifcopenshell.util.unit.get_project_unit(ifc_file, "VOLUMEUNIT"),
        }
        for key, value in self.project_units.items():
            if value:
                self.project_units[key] = (getattr(value, "Prefix", "None"), value.Name)

        self.si_names = {
            "IfcAreaMeasure": "SQUARE_METRE",
            "IfcLengthMeasure": "METRE",
            "IfcMassMeasure": "GRAM",
            "IfcTimeMeasure": "SECOND",
            "IfcVolumeMeasure": "CUBIC_METRE",
        }

    def convert(self, value: float, measure: str) -> float:
        if measure_unit := self.project_units.get(measure, None):
            return ifcopenshell.util.unit.convert(value, None, self.si_names[measure], *measure_unit)
        return value


class IteratorForTypes:
    """Currently ifcopenshell.geom.iterator support only IfcProducts, so this
    class is mimicking the iterator interface but works for IfcTypeProducts."""

    element: Union[ifcopenshell.entity_instance, None] = None
    shape: Union[ifcopenshell.geom.ShapeType, None] = None

    def __init__(
        self,
        ifc_file: ifcopenshell.file,
        settings: ifcopenshell.geom.settings,
        elements: Iterable[ifcopenshell.entity_instance],
    ):
        self.settings = settings
        self.elements = list(elements)
        self.element = None
        self.file = ifc_file
        model = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")
        assert model
        self.context = model

    def initialize(self) -> bool:
        return bool(self.next())

    def get_element_and_geometry(self) -> tuple[ifcopenshell.entity_instance, ifcopenshell.geom.ShapeType]:
        # get() is not implemented so it won't be confused with iteartor.get().
        # The difference is important since create_shape for product types
        # doesn't ouput SpapeElementType, only ShapeTypes.
        assert self.element and self.shape
        return (self.element, self.shape)

    def next(self) -> bool:
        if not self.elements:
            return False
        while self.elements:
            element = self.elements.pop()
            if self.process_shape(element):
                return True
        return False

    def process_shape(self, element: ifcopenshell.entity_instance):
        representation = ifcopenshell.util.representation.get_representation(element, self.context)
        if not representation:
            return False
        self.shape = ifcopenshell.geom.create_shape(self.settings, representation)
        self.element = element
        return True


class IfcOpenShell:
    """Calculates Model body context geometry using the default IfcOpenShell
    iterator on triangulation elements."""

    # Implementations are located in ifcopenshell.util.shape.
    raw_functions = {
        # IfcLengthMeasure
        "get_x": Function("IfcLengthMeasure", "X", "Calculates the length along the local X axis"),
        "get_y": Function("IfcLengthMeasure", "Y", "Calculates the length along the local Y axis"),
        "get_z": Function("IfcLengthMeasure", "Z", "Calculates the length along the local Z axis"),
        "get_max_xy": Function("IfcLengthMeasure", "Max XY", "The maximum X or Y local dimension"),
        "get_max_xyz": Function("IfcLengthMeasure", "Max XYZ", "The maximum X, Y, or Z local dimension"),
        "get_min_xyz": Function("IfcLengthMeasure", "Min XYZ", "The minimum X, Y, or Z local dimension"),
        "get_top_elevation": Function("IfcLengthMeasure", "Top elevation", "The local maximum Z ordinate"),
        "get_bottom_elevation": Function("IfcLengthMeasure", "Bottom Elevation", "The local minimum Z ordinate"),
        "get_footprint_perimeter": Function(
            "IfcLengthMeasure",
            "Footprint Perimeter",
            "The perimeter if the object's faces were projected along the Z-axis and seen top down",
        ),
        "get_segment_length": Function(
            "IfcLengthMeasure", "Segment Length", "Intelligently guesses the length of flow segments"
        ),
        # IfcAreaMeasure
        "get_area": Function("IfcAreaMeasure", "Area", "The total surface area of the element"),
        "get_footprint_area": Function(
            "IfcAreaMeasure",
            "Footprint Area",
            "The area if the object's faces were projected along the Z-axis and seen top down",
        ),
        "get_max_side_area": Function(
            "IfcAreaMeasure",
            "Max Side Area",
            "The maximum side area when seen from either X, Y, or Z directions",
        ),
        "get_outer_surface_area": Function(
            "IfcAreaMeasure",
            "Outer Surface Area",
            "The total surface area except for the top or bottom, such as the ends of columns or beams",
        ),
        "get_side_area": Function(
            "IfcAreaMeasure",
            "Side area",
            "The side (non-projected) are of the shape as seen from the local Y-axis",
        ),
        "get_top_area": Function(
            "IfcAreaMeasure",
            "Top area",
            "The total surface area deviating by no more than 45 degrees from the local Z+ axis.",
        ),
        # IfcVolumeMeasure
        "get_volume": Function("IfcVolumeMeasure", "Volume", "Calculates the volume of a manifold shape"),
    }

    functions = {}
    for k, v in raw_functions.items():
        functions[f"gross_{k}"] = Function(v.measure, f"Gross {v.name}", v.description)
        functions[f"net_{k}"] = Function(v.measure, f"Net {v.name}", v.description)

    @classmethod
    def calculate(
        cls,
        ifc_file: ifcopenshell.file,
        elements: set[ifcopenshell.entity_instance],
        qtos: dict[str, dict[str, Union[str, None]]],
        results: ResultsDict,
    ) -> None:
        formula_functions: dict[str, types.FunctionType] = {}

        cls.gross_settings = ifcopenshell.geom.settings()
        cls.gross_settings.set("disable-opening-subtractions", True)
        cls.net_settings = ifcopenshell.geom.settings()
        cls.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)

        gross_qtos: QtosFormulas = {}
        net_qtos: QtosFormulas = {}

        for name, quantities in qtos.items():
            for quantity, formula in quantities.items():
                if not formula:
                    continue
                gross_or_net_qtos = gross_qtos if formula.startswith("gross_") else net_qtos
                if formula.endswith("get_segment_length"):
                    gross_or_net_qtos.setdefault(name, {})[quantity] = formula.partition("_")[2]
                elif formula.startswith(("gross_", "net_")):
                    formula = formula.partition("_")[2]
                    gross_or_net_qtos.setdefault(name, {})[quantity] = formula
                    formula_functions[formula] = getattr(ifcopenshell.util.shape, formula)

        tasks: list[tuple[Union[ifcopenshell.geom.iterator, IteratorForTypes], QtosFormulas]] = []

        if gross_qtos:
            for iterator in IfcOpenShell.create_iterators(ifc_file, cls.gross_settings, list(elements)):
                tasks.append((iterator, gross_qtos))

        if net_qtos:
            for iterator in IfcOpenShell.create_iterators(ifc_file, cls.gross_settings, list(elements)):
                tasks.append((iterator, net_qtos))

        cls.unit_converter = SI2ProjectUnitConverter(ifc_file)

        for iterator, qtos_ in tasks:
            if iterator.initialize():
                while True:
                    if isinstance(iterator, ifcopenshell.geom.iterator):
                        shape = iterator.get()
                        geometry = shape.geometry
                        element = ifc_file.by_id(shape.id)
                    else:
                        element, geometry = iterator.get_element_and_geometry()

                    results.setdefault(element, {})
                    for name, quantities in qtos_.items():
                        results[element].setdefault(name, {})
                        for quantity, formula in quantities.items():
                            if formula == "get_segment_length":
                                results[element][name][quantity] = cls.get_segment_length(element)
                            else:
                                results[element][name][quantity] = cls.unit_converter.convert(
                                    formula_functions[formula](geometry),
                                    IfcOpenShell.raw_functions[formula].measure,
                                )
                    if not iterator.next():
                        break

    @staticmethod
    def create_iterators(
        ifc_file: ifcopenshell.file, settings: ifcopenshell.geom.settings, elements: list[ifcopenshell.entity_instance]
    ) -> list[Union[ifcopenshell.geom.iterator, IteratorForTypes]]:
        elements_sorted: defaultdict[bool, list[ifcopenshell.entity_instance]] = defaultdict(list)
        iterators = []
        for element in elements:
            elements_sorted[element.is_a("IfcTypeProduct")].append(element)
        if True in elements_sorted:
            iterators.append(IteratorForTypes(ifc_file, settings, elements_sorted[True]))
        if False in elements_sorted:
            iterators.append(
                ifcopenshell.geom.iterator(settings, ifc_file, multiprocessing.cpu_count(), include=elements)
            )
        return iterators

    @classmethod
    def get_segment_length(cls, element: ifcopenshell.entity_instance) -> float:
        rep = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        if rep and len(rep.Items or []) == 1 and rep.Items[0].is_a("IfcExtrudedAreaSolid"):
            item = rep.Items[0]
            if item.SweptArea.is_a("IfcRectangleProfileDef"):
                # Revit doesn't follow the +Z extrusion rule, so the rectangle isn't the cross section
                x = item.SweptArea.XDim
                y = item.SweptArea.YDim
                z = item.Depth
                return max([x, y, z])
            elif item.SweptArea.is_a("IfcCircleProfileDef"):
                return item.Depth
            elif item.SweptArea.is_a("IfcParameterizedProfileDef"):
                return item.Depth
            try:
                area_shape = ifcopenshell.geom.create_shape(settings, item.SweptArea)
            except:
                return
            x = ifcopenshell.util.shape.get_x(area_shape.geometry) / cls.unit_scale
            y = ifcopenshell.util.shape.get_y(area_shape.geometry) / cls.unit_scale
            z = item.Depth
            return max([x, y, z])


class Blender:
    """Calculates geometry based on currently loaded Blender objects."""

    functions = {
        # IfcLengthMeasure
        "get_covering_width": Function("IfcLengthMeasure", "Covering Width", ""),
        "get_finish_ceiling_height": Function("IfcLengthMeasure", "Finish Ceiling Height", ""),
        "get_finish_floor_height": Function("IfcLengthMeasure", "Finish Floor Height", ""),
        "get_gross_perimeter": Function("IfcLengthMeasure", "Gross Perimeter", ""),
        "get_height": Function("IfcLengthMeasure", "Height", ""),
        "get_length": Function("IfcLengthMeasure", "Length", ""),
        "get_opening_depth": Function("IfcLengthMeasure", "Opening Depth", ""),
        "get_opening_height": Function("IfcLengthMeasure", "Opening Height", ""),
        "get_rectangular_perimeter": Function("IfcLengthMeasure", "Rectangular Perimeter", ""),
        "get_stair_length": Function("IfcLengthMeasure", "Stair Length", ""),
        "get_width": Function("IfcLengthMeasure", "Width", ""),
        # IfcAreaMeasure
        "get_covering_gross_area": Function("IfcAreaMeasure", "Covering Gross Area", ""),
        "get_covering_net_area": Function("IfcAreaMeasure", "Covering Net Area", ""),
        "get_cross_section_area": Function("IfcAreaMeasure", "Cross Section Area", ""),
        "get_gross_ceiling_area": Function("IfcAreaMeasure", "Gross Ceiling Area", ""),
        "get_gross_footprint_area": Function("IfcAreaMeasure", "Gross Footprint Area", ""),
        "get_gross_side_area": Function("IfcAreaMeasure", "Gross Side Area", ""),
        "get_gross_stair_area": Function("IfcAreaMeasure", "Gross Stair Area", ""),
        "get_gross_surface_area": Function("IfcAreaMeasure", "Gross Surface Area", ""),
        "get_gross_top_area": Function("IfcAreaMeasure", "Gross Top Area", ""),
        "get_net_ceiling_area": Function("IfcAreaMeasure", "Net Ceiling Area", ""),
        "get_net_floor_area": Function("IfcAreaMeasure", "Net Floor Area", ""),
        "get_net_footprint_area": Function("IfcAreaMeasure", "Net Footprint Area", ""),
        "get_net_side_area": Function("IfcAreaMeasure", "Net Side Area", ""),
        "get_net_stair_area": Function("IfcAreaMeasure", "Net Stair Area", ""),
        "get_net_surface_area": Function("IfcAreaMeasure", "Net Surface Area", ""),
        "get_net_top_area": Function("IfcAreaMeasure", "Net Top Area", ""),
        "get_opening_mapping_area": Function("IfcAreaMeasure", "Opening Mapping Area", ""),
        "get_outer_surface_area": Function("IfcAreaMeasure", "Outer Surface Area", ""),
        # IfcVolumeMeasure
        "get_gross_volume": Function("IfcVolumeMeasure", "Gross Volume", ""),
        "get_net_volume": Function("IfcVolumeMeasure", "Net Volume", ""),
        "get_space_net_volume": Function("IfcVolumeMeasure", "Space Net Volume", ""),
        # IfcMassMeasure
        "get_gross_weight": Function("IfcMassMeasure", "Gross Weight", ""),
        "get_net_weight": Function("IfcMassMeasure", "Net Weight", ""),
    }

    @staticmethod
    def calculate(
        ifc_file: ifcopenshell.file,
        elements: set[ifcopenshell.entity_instance],
        qtos: dict[str, dict[str, Union[str, None]]],
        results: ResultsDict,
    ) -> None:
        import bonsai.tool as tool
        import bonsai.bim.module.qto.calculator as calculator

        unit_converter = SI2ProjectUnitConverter(ifc_file)
        formula_functions: dict[str, types.FunctionType] = {}

        for element in elements:
            obj = tool.Ifc.get_object(element)
            if not obj or obj.type != "MESH":
                continue
            element_results = {}
            for name, quantities in qtos.items():
                qto_results = {}
                for quantity, formula in quantities.items():
                    if not formula:
                        continue
                    if not (formula_function := formula_functions.get(formula)):
                        formula_function = formula_functions[formula] = getattr(calculator, formula)
                    if (value := formula_function(obj)) is not None:
                        qto_results[quantity] = unit_converter.convert(value, Blender.functions[formula].measure)
                if qto_results:
                    element_results[name] = qto_results
            # Avoid adding empty qsets if nothing was calculated.
            if element_results:
                results[element] = element_results


calculators = {"Blender": Blender, "IfcOpenShell": IfcOpenShell}
