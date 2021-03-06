##Copyright 2008-2011 Thomas Paviot (tpaviot@gmail.com)
##
##This file is part of pythonOCC.
##
##pythonOCC is free software: you can redistribute it and/or modify
##it under the terms of the GNU Lesser General Public License as published by
##the Free Software Foundation, either version 3 of the License, or
##(at your option) any later version.
##
##pythonOCC is distributed in the hope that it will be useful,
##but WITHOUT ANY WARRANTY; without even the implied warranty of
##MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
##GNU Lesser General Public License for more details.
##
##You should have received a copy of the GNU Lesser General Public License
##along with pythonOCC.  If not, see <http://www.gnu.org/licenses/>.



import os.path
from OCC.STEPCAFControl import STEPCAFControl_Reader
from OCC import XCAFApp, TDocStd, XCAFDoc
from OCC.TCollection import TCollection_ExtendedString, TCollection_AsciiString
from OCC.TDF import TDF_LabelSequence, TDF_Tool, TDF_Label
from OCC.Utils.Topology import Topo
from OCC.TDataStd import Handle_TDataStd_Name, TDataStd_Name_GetID
from OCC.Quantity import Quantity_Color
from OCC.Bnd import Bnd_Box
from OCC.TopLoc import TopLoc_Location
from OCC.BRepMesh import BRepMesh_Mesh
from OCC.BRepBndLib import BRepBndLib_Add
from OCC.BRep import BRep_Tool_Triangulation
from mesh import GeometryWriter, get_mesh_precision
from classes import (Link, Product, TransformationMatrix,  search_assembly,
        get_available_name)
from OCC.GarbageCollector import garbage


def new_collect_object(self, obj_deleted):
    self._kill_pointed()

garbage.collect_object=new_collect_object


class StepImporter(object):

    """

    :param file_path: Path of the file **.stp** to analyzing
    :param id: For the generation of the :class:`.Product`, **id** is assigned like product.doc_id for every node of :class:`.Product`. For the generation of the files of geometry **.geo**, **id** , together with an index generated by the class, are used to identify the content of every file



    Generates from a path of :class:`~django.core.files.File` **.stp**:


    -A set of files **.geo** that represents the geometry of the different simple products that are useful to realize the visualization 3D across the web browser.

    -A structure of information that represents the arborescencse of the different assemblies,represented in a  :class:`.Product` . (Including his spatial location and orientation and his label of reference (:class:`.OCC.TDF.TDF_Label`))


    This class is invoked from three different subprocesses related to the functionality of pythonOCC(generate3D.py , generateComposition.py , generateDecomposition.py).

   """

    def __init__(self, file_path, id=None):

        self.file = file_path.encode("utf-8")
        self.id = id
        self.shapes_simples = []
        self.main_product=None
        basename = os.path.basename(self.file)
        self.fileName = os.path.splitext(basename)[0]

        self.STEPReader = STEPCAFControl_Reader()

        if self.STEPReader.ReadFile(self.file) != 1:
            raise OCCReadingStepError

        self.h_doc = TDocStd.Handle_TDocStd_Document()
        self.app = XCAFApp.GetApplication().GetObject()
        self.app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), self.h_doc)

        self.STEPReader.Transfer(self.h_doc)

        self.doc = self.h_doc.GetObject()
        self.h_shape_tool = XCAFDoc.XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        self.h_colors_tool = XCAFDoc.XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        self.shape_tool = self.h_shape_tool.GetObject()
        self.color_tool=self.h_colors_tool.GetObject()

        self.shapes = TDF_LabelSequence()
        self.shape_tool.GetShapes(self.shapes)
        for i in range(self.shapes.Length()):
            shape = self.shapes.Value(i+1)
            if self.shape_tool.IsSimpleShape(shape):
                compShape = self.shape_tool.GetShape(shape)
                t = Topo(compShape)
                if t.number_of_vertices() > 0:
                    label = get_label_name(shape)
                    color = find_color(shape, self.color_tool, self.shape_tool)
                    self.shapes_simples.append(SimpleShape(label, compShape, color))

        roots = TDF_LabelSequence()
        self.shape_tool.GetFreeShapes(roots)
        self.thumbnail_valid = False
        if roots.Length() == 1:
            shape = self.shape_tool.GetShape(roots.Value(1))
            t = Topo(shape)
            if t.number_of_vertices() > 0:
                bbox = Bnd_Box()
                gap = 0
                bbox.SetGap(gap)

                BRepMesh_Mesh(shape, get_mesh_precision(shape, 1))
                faces_iterator = Topo(shape).faces()
                for F in faces_iterator:
                    face_location = TopLoc_Location()
                    BRep_Tool_Triangulation(F, face_location)
                BRepBndLib_Add(shape, bbox)
                x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()
                diagonal = max(x_max - x_min, y_max - y_min, z_max - z_min)
                if diagonal > 0:
                    self.scale = 200. / diagonal
                    self.trans = ((x_max - x_min) / 2. - x_max,
                                  (y_max - y_min) / 2. - y_max,
                                  (z_max - z_min) / 2. - z_max)
                    self.thumbnail_valid = True

        ws = self.STEPReader.Reader().WS().GetObject()
        model = ws.Model().GetObject()
        model.Clear()

    def compute_geometries(self,root_path, pov_dir):
        """

        :param root_path: Path where to store the files **.geo** generated

        When we generate a new :class:`.StepImporter` we will refill a list(**shapes_simples**) whit the :class:`.SimpleShape` contained in the file **.stp**

        For each :class:`.SimpleShape` in the list **shapes_simples**:

            We call the method :func:`.write_geometries` to generate a file **.geo** representative of its geometry,the content of the file is identified by the index+1 (>0) of the position of the :class:`.SimpleShape` in the list of **SimpleShapes**  and by the attribue id of :class:`.StepImporter`

        Returns the list of the path of the generated **.geo** files

        """
        files_index=""
        self.povs = []

        for index, shape in enumerate(self.shapes_simples):
            name=get_available_name(root_path,self.fileName+".geo")
            path=os.path.join(root_path, name)
            identifier="_"+str(index+1)+"_"+str(self.id)
            writer = GeometryWriter(shape, 0.3)
            pov_filename = os.path.join(pov_dir, os.path.basename(path + ".inc"))
            writer.write_geometries(identifier, path, pov_filename)
            files_index+="GEO:"+name+" , "+str(index+1)+"\n"
            if writer.triangle_count:
                self.povs.append((os.path.basename(name + ".inc"), identifier))

        return files_index

    def generate_product_arbre(self):
        """

        Generates a :class:`.Product` relative to the assemblies of the file **.stp**, for every node of the :class:`.Product` it includes a label (:class:`.OCC.TDF.TDF_Label`) that represents and identifies the node , openPLM can only work whit a single root **.stp** files

        """

        roots = TDF_LabelSequence()
        self.shape_tool.GetFreeShapes(roots)
        if roots.Length() != 1:
            raise MultiRootError

        deep = 0
        product_id = [1]
        root = roots.Value(1)
        name = get_label_name(root)
        self.main_product = Product(name, deep, root, self.id, product_id[0], self.file)
        product_id[0] += 1
        expand_product(self.shape_tool,self.main_product, self.shapes_simples,
                      deep+1, self.id, self.main_product, product_id, self.file)

        return self.main_product


class SimpleShape():
    """
    Class used to represent a simple shape geometry (not assembly)

    :model attributes:

    .. attribute:: name

        Name of geometry

    .. attribute:: TopoDS_Shape

        :class:`.OCC.TopoDS.TopoDS_Shape` that contains the geometry

    .. attribute:: color

        :class:`.OCC.Quantity.Quantity_Color` that contains information about color of geometry

    """

    def __init__(self, name,TopoDS_Shape,color):
        self.name = name
        self.shape = TopoDS_Shape #OCC.TopoDS.TopoDS_Shape
        self.color = color


def expand_product(shape_tool,product,shapes_simples,deep,doc_id,product_root,product_id,file_path):
    """
    :param shape_tool: :class:`.OCC.XCAFDoc.XCAFDoc_ShapeTool`
    :param product: :class:`.Product` that will be expanded
    :param shapes_simples: list of :class:`.SimpleShape`
    :param deep: Depth of the node that we explore
    :param doc_id: id that references a :class:`.DocumentFile` of which we are generating the :class:`.Product`
    :param product_root: :class:`.Product` root of the arborescense


    We are going to expand a :class:`.Product` (**product**) from the :class:`.OCC.TDF.TDF_Label` who identifies it (**product**.label_reference)


    If the **product** is an assembly:

        * We generate the **list** of the :class:`.OCC.TDF.TDF_Label` that define it

        * for each :class:`.OCC.TDF.TDF_Label` in **list**:

            - We generate a new :class:`.document3D.classes.Link`  or if two or more :class:`.OCC.TDF.TDF_Label` of the list are partner, add an occurrence extra to the :class:`.document3D.classes.Link` that already was generated

            - The :class:`.document3D.classes.Link` is going to point at a new :class:`.Product` or, if the :class:`.Product` is already present in **product_root**, at the already definite product

            - If the :class:`.document3D.classes.Link` pointed at a new :class:`.Product` we need to expand it

                * The atribute **label_reference** of the new :class:`.Product` should be the :class:`.OCC.TDF.TDF_Label`

                * We expand the :class:`.Product` in a recursive call of method


    Else the **product** is a simple shape:

       * We look in the list of **shapes_simples** for his partner

       * We set the attribute **product**.geometry like the index+1 (>0) of the position of his partner in the list of **SimpleShape**

    """
    if shape_tool.IsAssembly(product.label_reference):
        l_c = TDF_LabelSequence()
        shape_tool.GetComponents(product.label_reference,l_c)
        for i in range(l_c.Length()):
            label = l_c.Value(i+1)
            if shape_tool.IsReference(label):
                label_reference=TDF_Label()
                shape_tool.GetReferredShape(label, label_reference)
                reference_found = False
                matrix = TransformationMatrix(get_matrix(shape_tool.GetLocation(label).Transformation()))
                shape = shape_tool.GetShape(label_reference)
                for link in product.links:
                    if shape_tool.GetShape(link.product.label_reference).IsPartner(shape):
                        link.add_occurrence(get_label_name(label), matrix)
                        reference_found = True
                        break

                if not reference_found:
                    new_product = Product(get_label_name(label_reference), deep,
                                          label_reference, doc_id, product_id[0], file_path)
                    product_assembly = search_assembly(new_product, product_root)
                    if product_assembly:
                        product.links.append(Link(product_assembly))
                    else:
                        product.links.append(Link(new_product))
                        product_id[0] += 1
                        expand_product(shape_tool, new_product, shapes_simples,
                            deep+1, doc_id, product_root,product_id, file_path)


                    product.links[-1].add_occurrence(get_label_name(label), matrix)
    else:
        compShape = shape_tool.GetShape(product.label_reference)
        for index in range(len(shapes_simples)):
            if compShape.IsPartner(shapes_simples[index].shape):
                product.set_geometry(index+1) #to avoid index==0


def get_matrix(location):
    """
    Converts *location* in an array ([x1, x2, x3, x4, y1, y2, y3, y4, z1, z2, z3, z4])

    == == == == == = ==
    x1 x2 x3 x4  x = x'
    y1 y2 y3 y4  y = y'
    z1 z2 z3 z4  z = z'
    0  0  0  1   1 = 1
    == == == == == = ==

    :param location: location
    :type location: :class:`.OCC.TopLoc.TopLoc_Location`

    """

    m = location.VectorialPart()
    gp = m.Row(1)
    x1 = gp.X()
    x2 = gp.Y()
    x3 = gp.Z()
    x4 = location.Transforms()[0]
    gp = m.Row(2)
    y1 = gp.X()
    y2 = gp.Y()
    y3 = gp.Z()
    y4 = location.Transforms()[1]
    gp = m.Row(3)
    z1 = gp.X()
    z2 = gp.Y()
    z3 = gp.Z()
    z4 = location.Transforms()[2]
    return [x1, x2, x3, x4, y1, y2, y3, y4, z1, z2, z3, z4]

def get_label_name(lab):
    """

    Returns the name of a :class:`.OCC.TDF.TDF_Label` (**lab**)

    :param lab: :class:`.OCC.TDF.TDF_Label`

    """
    entry = TCollection_AsciiString()
    TDF_Tool.Entry(lab,entry)
    N = Handle_TDataStd_Name()
    lab.FindAttribute(TDataStd_Name_GetID(),N)
    n=N.GetObject()
    return unicode(n.Get().PrintToString(),errors='ignore')#.decode("latin-1")


def set_label_name(label, name):
    """

    Sets the name of a :class:`.OCC.TDF.TDF_Label` (**label**)

    :param label: :class:`.OCC.TDF.TDF_Label`
    :param name: new name for a :class:`.OCC.TDF.TDF_Label`

    """
    entry = TCollection_AsciiString()
    TDF_Tool.Entry(label, entry)
    N = Handle_TDataStd_Name()
    label.FindAttribute(TDataStd_Name_GetID(),N)
    n=N.GetObject()
    n.Set(TCollection_ExtendedString(name.encode("latin-1")))


def find_color(label, color_tool, shape_tool):
    """

    Gets the color of a :class:`.OCC.TDF.TDF_Label` (**label**)

    :param label: :class:`.OCC.TDF.TDF_Label`
    :param shape_tool: :class:`.OCC.XCAFDoc.XCAFDoc_ShapeTool`
    :param color_tool: :class:`.OCC.XCAFDoc.XCAFDoc_ColorTool`

    """
    c = Quantity_Color()
    if (color_tool.GetInstanceColor(shape_tool.GetShape(label), 0, c) or
        color_tool.GetInstanceColor(shape_tool.GetShape(label), 1, c) or
        color_tool.GetInstanceColor(shape_tool.GetShape(label), 2, c)):
        for i in (0, 1, 2):
            color_tool.SetInstanceColor(label, i, c)
        return c

    if (color_tool.GetColor(label, 0, c) or
        color_tool.GetColor(label, 1, c) or
        color_tool.GetColor(label, 2, c)):
        shape = shape_tool.GetShape(label)
        for i in (0, 1, 2):
            color_tool.SetInstanceColor(shape, i, c)
        return c

    return False


class MultiRootError(Exception):
    def __unicode__(self):
        return u"OpenPLM does not support files step with multiple roots"


class OCCReadingStepError(Exception):
    def __unicode__(self):
        return u"PythonOCC could not read the file"

