# IfcOpenShell - IFC toolkit and geometry engine
# Copyright (C) 2021 Thomas Krijnen <thomas@aecgeeks.com>
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


import math
from typing import Sequence

import numpy as np

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.guid
import ifcopenshell.template
from ifcopenshell import entity_instance
from ifcopenshell import ifcopenshell_wrapper


def evaluate_representation(shape_rep: entity_instance, dist_along: float) -> np.ndarray:
    """
    Calculate the 4x4 geometric transform at a point on an alignment segment
    @param shape_rep: The representation shape (composite curve, gradient curve, or segmented reference curve) to evaluate
    @param dist_along: The distance along this representation at the point of interest (point to be calculated)
    """
    supported_rep_types = ["IFCCOMPOSITECURVE", "IFCGRADIENTCURVE", "IFCSEGMENTEDREFERENCECURVE"]
    shape_rep_type = shape_rep.is_a().upper()
    if not shape_rep_type in supported_rep_types:
        raise NotImplementedError(
            f"Expected entity type to be one of {[_ for _ in supported_rep_types]}, got '{shape_rep_type}"
        )

    # TODO: confirm point is not beyond limits of alignment

    s = ifcopenshell.geom.settings()
    piecewise_function = ifcopenshell_wrapper.map_shape(s, shape_rep.wrapped_data)
    pwf_evaluator = ifcopenshell_wrapper.piecewise_function_evaluator(piecewise_function, s)

    trans_matrix = pwf_evaluator.evaluate(dist_along)

    return np.array(trans_matrix, dtype=np.float64).T


def evaluate_segment(segment: entity_instance, dist_along: float) -> np.ndarray:
    """
    Calculate the 4x4 geometric transform at a point on an alignment segment
    @param segment: The segment containing the point that we would like to
    @param dist_along: The distance along this segment at the point of interest (point to be calculated)
    """
    supported_segment_types = ["IFCCURVESEGMENT"]
    segment_type = segment.is_a().upper()
    if not segment_type in supported_segment_types:
        raise NotImplementedError(f"Expected entity type 'IFCCURVESEGMENT', got '{segment_type}")
    if dist_along > segment.SegmentLength:
        raise ValueError(f"Provided value {dist_along=} is beyond the end of the segment ({segment.SegmentLength}).")

    s = ifcopenshell.geom.settings()
    piecewise_function = ifcopenshell_wrapper.map_shape(s, segment.wrapped_data)
    pwf_evaluator = ifcopenshell_wrapper.piecewise_function_evaluator(piecewise_function, s)

    trans_matrix = pwf_evaluator.evaluate(dist_along)

    return np.array(trans_matrix, dtype=np.float64).T


def generate_vertices(rep_curve: entity_instance, distance_interval: float = 5.0) -> np.ndarray:
    """
    Generate vertices along an alignment

    @param rep_curve: The alignment's representation curve to use to generate vertices.

    Note: rep_curve must be IfcCompositeCurve, IfcGradientCurve, or IfcSegmentedReferenceCurve

    @param distance_interval: The distance between points along the alignment at which to generate the points
    """
    if rep_curve is None:
        raise ValueError("Alignment representation not found.")

    s = ifcopenshell.geom.settings()
    s.set("PIECEWISE_STEP_PARAM", distance_interval)
    shape = ifcopenshell.geom.create_shape(s, rep_curve)
    vertices = shape.verts
    if len(vertices) == 0:
        msg = f"[ERROR] No vertices generated by ifcopenshell.geom.create_shape()."
        raise ValueError(msg)
    return np.array(vertices).reshape((-1, 3))


def print_structure(alignment, indent=0):
    """
    Debugging function to print alignment decomposition
    """
    print(" " * indent, str(alignment)[0:100])
    for rel in alignment.IsNestedBy:
        for child in rel.RelatedObjects:
            print_structure(child, indent + 2)


def name_segments(prefix: str, segments: Sequence[entity_instance]) -> None:
    """
    Sets the segment name like ("H1" for horizontal, "V1" for vertical, "C1" for cant)
    """
    for i, segment in enumerate(segments):
        segment.Name = f"{prefix}{i + 1}"


class IfcAlignmentHelper:
    """
    Create a new IfcAlignment including horizontal and vertical alignments by PI points.

    Currently only supports horizontal lines and circular arcs (no spirals or other transitions)
    Currently only supports parabolic vertical curves.
    Does not yet accommodate cant alignment considerations.
    """

    # TODO: add missing functionality noted in the docstring

    def __init__(
        self,
        file: ifcopenshell.file = None,
        filename: str = None,
        creator: str = None,
        organization: str = None,
        application: str = None,
        project_globalid=None,
        project_name: str = None,
    ):
        """
        @param file: An existing model that the alignment will be added to
        @param filename: Name for a new model to be created that will contain the alignment
        @param creator: Name of the actor creating the file
        @param organization: Name of the creator's organization
        @param application: Name of the authoring application
        @param project_globalid: value for the file's IfcProject.GlobalId attribute
        @param project_name: value for the file's IfcProject.Name attribute
        """
        if file is None:
            self._file = ifcopenshell.template.create(
                filename=filename,
                creator=creator,
                organization=organization,
                application=application,
                project_globalid=project_globalid,
                project_name=project_name,
                schema_identifier="IFC4X3_ADD2",
            )
        else:
            self._file = file

        self._geom_context = self._file.by_type("IfcGeometricRepresentationContext")[0]
        self._axis_geom_subcontext = self._file.createIfcGeometricRepresentationSubContext(
            ContextIdentifier="Axis", ContextType="Model", ParentContext=self._geom_context, TargetView="GRAPH_VIEW"
        )

    def _create_segment_representations(
        self,
        global_placement: entity_instance,
        curve_segments: Sequence[entity_instance],
        segments: Sequence[entity_instance],
    ):
        for curve_segment, alignment_segment in zip(curve_segments, segments):
            axis_representation = self._file.create_entity(
                type="IfcShapeRepresentation",
                ContextOfItems=self._axis_geom_subcontext,
                RepresentationIdentifier="Axis",
                RepresentationType="Segment",
                Items=(curve_segment,),
            )
            product = self._file.create_entity(
                type="IfcProductDefinitionShape", Name=None, Description=None, Representations=(axis_representation,)
            )
            alignment_segment.ObjectPlacement = global_placement
            alignment_segment.Representation = product

    def _map_alignment_horizontal_segment(self, segment: entity_instance) -> Sequence[entity_instance]:
        segment_type = segment.is_a().upper()
        expected_type = "IFCALIGNMENTHORIZONTALSEGMENT"
        if not segment_type == expected_type:
            raise TypeError(f"Expected to see type '{expected_type}', instead received '{segment_type}'.")

        start_point = segment.StartPoint
        start_direction = segment.StartDirection
        start_radius = segment.StartRadiusOfCurvature
        length = segment.SegmentLength
        _type = segment.PredefinedType

        if math.isclose(length, 0):
            # set transition value based on whether this is the final zero-length segment
            transition = "DISCONTINUOUS"
        else:
            transition = "CONTSAMEGRADIENTSAMECURVATURE"

        if _type == "LINE":
            parent_curve = self._file.create_entity(
                type="IfcLine",
                Pnt=self._file.create_entity(
                    type="IfcCartesianPoint",
                    Coordinates=(0.0, 0.0),
                ),
                Dir=self._file.create_entity(
                    type="IfcVector",
                    Orientation=self._file.create_entity(
                        type="IfcDirection",
                        DirectionRatios=(1.0, 0.0),
                    ),
                    Magnitude=1.0,
                ),
            )
            curve_segment = self._file.create_entity(
                type="IfcCurveSegment",
                Transition=transition,
                Placement=self._file.create_entity(
                    type="IfcAxis2Placement2D",
                    Location=start_point,
                    RefDirection=self._file.createIfcDirection(
                        (math.cos(start_direction), math.sin(start_direction)),
                    ),
                ),
                SegmentStart=self._file.createIfcLengthMeasure(0.0),
                SegmentLength=self._file.createIfcLengthMeasure(length),
                ParentCurve=parent_curve,
            )
            result = (curve_segment, None)
        elif _type == "CIRCULARARC":
            parent_curve = self._file.createIfcCircle(
                Position=self._file.createIfcAxis2Placement2D(
                    Location=self._file.createIfcCartesianPoint(Coordinates=(0.0, 0.0)),
                    RefDirection=self._file.createIfcDirection((math.cos(start_direction), math.sin(start_direction))),
                ),
                Radius=abs(start_radius),
            )

            curve_segment = self._file.create_entity(
                type="IfcCurveSegment",
                Transition=transition,
                Placement=self._file.create_entity(
                    type="IfcAxis2Placement2D",
                    Location=start_point,
                    RefDirection=self._file.createIfcDirection((math.cos(start_direction), math.sin(start_direction))),
                ),
                SegmentStart=self._file.createIfcLengthMeasure(0.0),
                SegmentLength=self._file.createIfcLengthMeasure(length * start_radius / abs(start_radius)),
                ParentCurve=parent_curve,
            )
            result = (curve_segment, None)

        else:
            result = (None, None)

        return result

    def _create_horizontal_alignment(
        self,
        name: str,
        description: str,
        points: Sequence[Sequence[float]],
        radii: Sequence[float],
        include_geometry: bool = True,
    ):
        """
        Create a horizontal alignment using the PI layout method.

        @param name: value for Name attribute
        @param description: value for Description attribute
        @param points: (X, Y) pairs denoting the location of the horizontal PIs, including start (POB) and end (POE).
        @param radii: radii values to use for transition
        @param include_geometry: optionally create the alignment geometric representation as well as the semantic business logic
        """
        horizontal_segments = list()  # business logic
        horizontal_curve_segments = list()  # geometry

        xBT, yBT = points[0]
        xPI, yPI = points[1]

        i = 1

        for radius in radii:
            # back tangent
            dxBT = xPI - xBT
            dyBT = yPI - yBT
            angleBT = math.atan2(dyBT, dxBT)
            lengthBT = math.sqrt(dxBT * dxBT + dyBT * dyBT)

            # forward tangent
            i += 1
            xFT, yFT = points[i]
            dxFT = xFT - xPI
            dyFT = yFT - yPI
            angleFT = math.atan2(dyFT, dxFT)

            delta = angleFT - angleBT

            tangent = abs(radius * math.tan(delta / 2))

            lc = abs(radius * delta)

            radius *= delta / abs(delta)

            xPC = xPI - tangent * math.cos(angleBT)
            yPC = yPI - tangent * math.sin(angleBT)

            xPT = xPI + tangent * math.cos(angleFT)
            yPT = yPI + tangent * math.sin(angleFT)

            tangent_run = lengthBT - tangent

            # create back tangent run
            pt = self._file.create_entity(
                type="IfcCartesianPoint",
                Coordinates=(xBT, yBT),
            )
            design_parameters = self._file.create_entity(
                type="IfcAlignmentHorizontalSegment",
                StartTag=None,
                EndTag=None,
                StartPoint=pt,
                StartDirection=angleBT,
                StartRadiusOfCurvature=0.0,
                EndRadiusOfCurvature=0.0,
                SegmentLength=tangent_run,
                GravityCenterLineHeight=None,
                PredefinedType="LINE",
            )
            alignment_segment = self._file.create_entity(
                type="IfcAlignmentSegment",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=None,
                Name=None,
                Description=None,
                ObjectType=None,
                ObjectPlacement=None,
                Representation=None,
                DesignParameters=design_parameters,
            )
            horizontal_segments.append(alignment_segment)

            if include_geometry:
                horizontal_curve_segments.append(self._map_alignment_horizontal_segment(design_parameters)[0])

            # create circular curve
            pc = self._file.create_entity(
                type="IfcCartesianPoint",
                Coordinates=(xPC, yPC),
            )
            design_parameters = self._file.create_entity(
                type="IfcAlignmentHorizontalSegment",
                StartTag=None,
                EndTag=None,
                StartPoint=pc,
                StartDirection=angleBT,
                StartRadiusOfCurvature=float(radius),
                EndRadiusOfCurvature=float(radius),
                SegmentLength=lc,
                GravityCenterLineHeight=None,
                PredefinedType="CIRCULARARC",
            )
            alignment_segment = self._file.create_entity(
                type="IfcAlignmentSegment",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=None,
                Name=None,
                Description=None,
                ObjectType=None,
                ObjectPlacement=None,
                Representation=None,
                DesignParameters=design_parameters,
            )
            horizontal_segments.append(alignment_segment)

            if include_geometry:
                horizontal_curve_segments.append(self._map_alignment_horizontal_segment(design_parameters)[0])

            xBT = xPT
            yBT = yPT
            xPI = xFT
            yPI = yFT

        # done processing radii
        # create last tangent run
        dx = xPI - xBT
        dy = yPI - yBT
        angleBT = math.atan2(dy, dx)
        tangent_run = math.sqrt(dx * dx + dy * dy)
        pt = self._file.create_entity(type="IfcCartesianPoint", Coordinates=(xBT, yBT))

        design_parameters = self._file.create_entity(
            type="IfcAlignmentHorizontalSegment",
            StartTag=None,
            EndTag=None,
            StartPoint=pt,
            StartDirection=angleBT,
            StartRadiusOfCurvature=0.0,
            EndRadiusOfCurvature=0.0,
            SegmentLength=tangent_run,
            GravityCenterLineHeight=None,
            PredefinedType="LINE",
        )
        alignment_segment = self._file.create_entity(
            type="IfcAlignmentSegment",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=None,
            Name=None,
            Description=None,
            ObjectType=None,
            ObjectPlacement=None,
            Representation=None,
            DesignParameters=design_parameters,
        )
        horizontal_segments.append(alignment_segment)
        if include_geometry:
            horizontal_curve_segments.append(self._map_alignment_horizontal_segment(design_parameters)[0])

        # create zero length terminator segment
        poe = self._file.create_entity(type="IfcCartesianPoint", Coordinates=(xPI, yPI))

        design_parameters = self._file.create_entity(
            type="IfcAlignmentHorizontalSegment",
            StartTag="POE",
            EndTag="POE",
            StartPoint=poe,
            StartDirection=angleBT,
            StartRadiusOfCurvature=0.0,
            EndRadiusOfCurvature=0.0,
            SegmentLength=0.0,
            GravityCenterLineHeight=None,
            PredefinedType="LINE",
        )
        alignment_segment = self._file.create_entity(
            type="IfcAlignmentSegment",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=None,
            Name=None,
            Description=None,
            ObjectType=None,
            ObjectPlacement=None,
            Representation=None,
            DesignParameters=design_parameters,
        )
        horizontal_segments.append(alignment_segment)
        if include_geometry:
            horizontal_curve_segments.append(self._map_alignment_horizontal_segment(design_parameters)[0])

        if include_geometry:
            composite_curve = self._file.create_entity(
                type="IfcCompositeCurve",
                Segments=horizontal_curve_segments,
                SelfIntersect=False,
            )
        else:
            composite_curve = None

        return horizontal_segments, horizontal_curve_segments, composite_curve

    def _add_horizontal_alignment(
        self,
        alignment_name: str,
        points: Sequence[Sequence[float]],
        radii: Sequence[float],
        include_geometry: bool = True,
        alignment_description: str = None,
        start_station: float = 1000.0,
    ):
        horizontal_segments, horizontal_curve_segments, composite_curve = self._create_horizontal_alignment(
            alignment_name,
            alignment_description,
            points,
            radii,
            include_geometry,
        )

        name_segments(prefix="H", segments=horizontal_segments)

        # Create the horizontal alignment (IfcAlignmentHorizontal) and nest alignment segments
        horizontal_alignment = self._file.create_entity(
            type="IfcAlignmentHorizontal",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=None,
            Name=f"{alignment_name} - Horizontal",
            Description=alignment_description,
            ObjectType=None,
            ObjectPlacement=None,
            Representation=None,
        )

        nests_horizontal_segments = self._file.create_entity(
            type="IfcRelNests",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=None,
            Name="Nests horizontal alignment segments under horizontal alignment",
            RelatingObject=horizontal_alignment,
            RelatedObjects=horizontal_segments,
        )

        placement = self._file.createIfcLocalPlacement(
            PlacementRelTo=None,
            RelativePlacement=self._file.createIfcAxis2Placement2D(
                Location=self._file.createIfcCartesianPoint(Coordinates=(0.0, 0.0))
            ),
        )

        # create the alignment
        alignment = self._file.create_entity(
            type="IfcAlignment",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=None,
            Name=alignment_name,
            Description=alignment_description,
            ObjectType=None,
            ObjectPlacement=placement,
            Representation=None,
            PredefinedType=None,
        )

        # create geometric representation
        if include_geometry:
            # create the footprint representation
            footprint_shape_representation = self._file.create_entity(
                type="IfcShapeRepresentation",
                ContextOfItems=self._axis_geom_subcontext,
                RepresentationIdentifier="FootPrint",
                RepresentationType="Curve2D",
                Items=(composite_curve,),
            )

            # create the alignment product definition
            product_definition_shape = self._file.create_entity(
                type="IfcProductDefinitionShape",
                Name="Alignment Product Definition Shape",
                Description=None,
                Representations=(footprint_shape_representation,),
            )

            # create representations for each segment
            self._create_segment_representations(placement, horizontal_curve_segments, horizontal_segments)

            # add the representation to the alignment
            alignment.Representation = product_definition_shape

            # create referent for start station
            start_referent = self._file.createIfcReferent(
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=None,
                Name="Start Station",
                Description=None,
                ObjectType=None,
                ObjectPlacement=self._file.createIfcLinearPlacement(
                    RelativePlacement=self._file.createIfcAxis2PlacementLinear(
                        Location=self._file.createIfcPointByDistanceExpression(
                            DistanceAlong=self._file.createIfcLengthMeasure(0.0),
                            OffsetLateral=None,
                            OffsetVertical=None,
                            OffsetLongitudinal=None,
                            BasisCurve=composite_curve,
                        ),
                    ),
                    CartesianPosition=None,
                ),
                Representation=None,
                PredefinedType="STATION",
            )

            # nest the horizontal and the referent under the alignment
            nesting_of_alignment = self._file.create_entity(
                type="IfcRelNests",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=None,
                Name="Nests horizontal alignment and referents under overall alignment",
                RelatingObject=alignment,
                RelatedObjects=(horizontal_alignment, start_referent),
            )

            # aggregate the horizontal under the project
            project = self._file.by_type("IfcProject")[0]
            alignment_within_project = self._file.createIfcRelAggregates(
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=None,
                Name="Aggregates alignment under the project",
                RelatingObject=project,
                RelatedObjects=(alignment,),
            )

        return alignment

    def add_vertical_alignment(
        self,
        name: str,
        description: str,
        vpoints: Sequence[Sequence[float]],
        vclengths: Sequence[Sequence[float]],
        include_geometry: bool = True,
    ):
        """
        Create a vertical alignment using the PI layout method.

        @param name: value for Name attribute
        @param description: value for Description attribute
        @param vpoints: (distance_along, Z_height) pairs denoting the location of the vertical PIs, including start and end.
        @param vclengths: radii values to use for transition
        @param include_geometry: optionally create the alignment geometric representation as well as the semantic business logic
        """
        pass

    def add_alignment(
        self,
        name: str,
        hpoints: Sequence[Sequence[float]],
        radii: Sequence[float],
        include_geometry: bool = True,
        description: str = None,
        start_station: float = 1000.0,
    ):
        """
        Create a new alignment with a horizontal alignment using the PI layout method
        """
        self._add_horizontal_alignment(
            alignment_name=name,
            points=hpoints,
            radii=radii,
            include_geometry=include_geometry,
            alignment_description=description,
            start_station=start_station,
        )

    def save_file(self, filename) -> None:
        self._file.write(filename)


if __name__ == "__main__":
    import sys
    from matplotlib import pyplot as plt

    f = ifcopenshell.open(sys.argv[1])
    print_structure(f.by_type("IfcAlignment")[0])

    al_hor_rep = f.by_type("IfcCompositeCurve")[0]

    xy = generate_vertices(rep_curve=al_hor_rep, distance_interval=10.0)

    plt.plot(xy[0], xy[1])
    plt.savefig("horizontal_alignment.png")
