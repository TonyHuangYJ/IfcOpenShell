# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2020, 2021 Dion Moult <dion@thinkmoult.com>
#
# This file is part of Bonsai.
#
# Bonsai is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Bonsai is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Bonsai.  If not, see <http://www.gnu.org/licenses/>.

import bpy
import json
import ifcopenshell.api
import ifcopenshell.api.layer
import ifcopenshell.util.element
import ifcopenshell.util.attribute
import bonsai.bim.helper
import bonsai.tool as tool
from bonsai.bim.ifc import IfcStore


class LoadLayers(bpy.types.Operator):
    bl_idname = "bim.load_layers"
    bl_label = "Load Layers"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self.file = IfcStore.get_file()
        props = context.scene.BIMLayerProperties
        props.layers.clear()
        for layer in tool.Ifc.get().by_type("IfcPresentationLayerAssignment"):
            new = props.layers.add()
            new.name = layer.Name or "Unnamed"
            new.ifc_definition_id = layer.id()
            if layer.is_a("IfcPresentationLayerWithStyle"):
                new.with_style = True
                # IfcLogical can also be UNKNOWN, not just bool.
                ifc_logical_is_true = lambda x: x is True
                new["on"] = ifc_logical_is_true(layer.LayerOn)
                new["frozen"] = ifc_logical_is_true(layer.LayerFrozen)
                new["blocked"] = ifc_logical_is_true(layer.LayerBlocked)
        props.is_editing = True
        bpy.ops.bim.disable_editing_layer()
        return {"FINISHED"}


class DisableLayerEditingUI(bpy.types.Operator):
    bl_idname = "bim.disable_layer_editing_ui"
    bl_label = "Disable Layer Editing UI"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.BIMLayerProperties.is_editing = False
        return {"FINISHED"}


class EnableEditingLayer(bpy.types.Operator):
    bl_idname = "bim.enable_editing_layer"
    bl_label = "Enable Editing Layer"
    bl_options = {"REGISTER", "UNDO"}
    layer: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.BIMLayerProperties
        props.layer_attributes.clear()
        bonsai.bim.helper.import_attributes2(tool.Ifc.get().by_id(self.layer), props.layer_attributes)
        props.active_layer_id = self.layer
        return {"FINISHED"}


class DisableEditingLayer(bpy.types.Operator):
    bl_idname = "bim.disable_editing_layer"
    bl_label = "Disable Editing Layer"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.BIMLayerProperties.active_layer_id = 0
        return {"FINISHED"}


class AddPresentationLayer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_presentation_layer"
    bl_label = "Add Layer"
    bl_description = "Add new presentation layer."
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        props = context.scene.BIMLayerProperties
        ifc_file = tool.Ifc.get()
        if props.layer_type == "IfcPresentationLayerWithStyle":
            layer = ifcopenshell.api.layer.add_layer_with_style(ifc_file)
        else:
            layer = ifcopenshell.api.layer.add_layer(ifc_file)
        bpy.ops.bim.load_layers()
        bpy.ops.bim.enable_editing_layer(layer=layer.id())
        return {"FINISHED"}


class EditPresentationLayer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_presentation_layer"
    bl_label = "Edit Layer"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        props = context.scene.BIMLayerProperties
        attributes = bonsai.bim.helper.export_attributes(props.layer_attributes)
        ifc_file = tool.Ifc.get()
        ifcopenshell.api.layer.edit_layer(ifc_file, layer=ifc_file.by_id(props.active_layer_id), attributes=attributes)
        bpy.ops.bim.load_layers()
        return {"FINISHED"}


class RemovePresentationLayer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_presentation_layer"
    bl_label = "Remove Presentation Layer"
    bl_options = {"REGISTER", "UNDO"}
    layer: bpy.props.IntProperty()

    def _execute(self, context):
        props = context.scene.BIMLayerProperties
        self.file = IfcStore.get_file()
        ifcopenshell.api.run("layer.remove_layer", self.file, **{"layer": self.file.by_id(self.layer)})
        bpy.ops.bim.load_layers()
        return {"FINISHED"}


class AssignPresentationLayer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.assign_presentation_layer"
    bl_label = "Assign Presentation Layer"
    bl_description = "Assign presentation layer to the active representation of the active object"
    bl_options = {"REGISTER", "UNDO"}
    item: bpy.props.StringProperty()
    layer: bpy.props.IntProperty()

    def _execute(self, context):
        item = bpy.data.meshes.get(self.item) if self.item else context.active_object.data
        self.file = IfcStore.get_file()
        ifcopenshell.api.run(
            "layer.assign_layer",
            self.file,
            **{
                "items": [self.file.by_id(tool.Geometry.get_mesh_props(item).ifc_definition_id)],
                "layer": self.file.by_id(self.layer),
            },
        )
        return {"FINISHED"}


class UnassignPresentationLayer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.unassign_presentation_layer"
    bl_label = "Unassign Presentation Layer"
    bl_description = "Unassign presentation layer from the active representation of the active object"
    bl_options = {"REGISTER", "UNDO"}
    item: bpy.props.StringProperty()
    layer: bpy.props.IntProperty()

    def _execute(self, context):
        item = bpy.data.meshes.get(self.item) if self.item else context.active_object.data
        ifc_file = tool.Ifc.get()
        representation = tool.Geometry.get_data_representation(item)
        assert representation
        ifcopenshell.api.layer.unassign_layer(ifc_file, items=[representation], layer=ifc_file.by_id(self.layer))
        return {"FINISHED"}


class SelectLayerProducts(bpy.types.Operator):
    bl_idname = "bim.select_layer_products"
    bl_label = "Select Layer Products"
    bl_options = {"REGISTER", "UNDO"}
    layer: bpy.props.IntProperty()

    def execute(self, context):
        elements = ifcopenshell.util.element.get_elements_by_layer(tool.Ifc.get(), tool.Ifc.get().by_id(self.layer))
        for obj in context.visible_objects:
            obj.select_set(False)
            element = tool.Ifc.get_entity(obj)
            if element and element in elements:
                obj.select_set(True)
        return {"FINISHED"}


class SelectLayerInLayerUI(bpy.types.Operator):
    bl_idname = "bim.layer_ui_select"
    bl_label = "Select Layer In Layers UI"
    bl_options = {"REGISTER", "UNDO"}
    layer_id: bpy.props.IntProperty()

    def execute(self, context):
        props = tool.Layer.get_layer_props()
        ifc_file = tool.Ifc.get()
        layer = ifc_file.by_id(self.layer_id)
        bpy.ops.bim.load_layers()
        props.active_layer_index = next((i for i, m in enumerate(props.layers) if m.ifc_definition_id == self.layer_id))
        self.report(
            {"INFO"},
            f"Layer '{layer.Name or 'Unnamed'}' is selected in Layers UI.",
        )
        return {"FINISHED"}
