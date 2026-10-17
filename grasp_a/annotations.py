"""World-anchored A/B video guides. No physics or runtime object tracking.

All positions come from the frozen task definition. The screen-space guides
are deliberately always visible, including when a robot occludes a landmark.
They are reference locations, not measured cup positions or planned paths.
"""
from functools import lru_cache
import math

import numpy as np
from PIL import ImageDraw, ImageFont

ORANGE = (255, 172, 48)
GREEN = (80, 240, 142)
BLUE = (105, 191, 255)
OUTLINE = (21, 32, 46)


def sequence_annotations(spec):
    """Serializable display contract; changing it never changes task scoring."""
    task = spec["task_id"]
    a = list(spec["point_A"])
    b = list(spec["point_B"])
    config = spec["config"]
    extra = []
    title = {"transport_place":"TRANSPORT AND PLACE",
             "handover_transfer":"PHYSICAL HANDOVER", "pour_water":"POUR WATER"}[task]
    note = "Fixed task references, not a planned path. Coordinates in metres."
    b_label = "B PLACE"
    ring_radius = config["placement_tolerance_m"]
    ring_z = .602
    b_anchor = b.copy()
    if task == "handover_transfer":
        handoff = list(spec["handoff_pose"])
        b = [*handoff[:2], handoff[2]+config["receiver_lift_m"]]
        b_anchor = b.copy()
        ring_z = b[2]
        b_label = "B FINAL HOLD"
        extra.append(dict(kind="handoff",point=handoff))
        note = (f"B = final receiver hold; handoff z={handoff[2]:.3f} m, "
                f"then receiver lifts {config['receiver_lift_m']:.3f} m.")
    elif task == "pour_water":
        b_label = "B RECEIVER"
        b_anchor = [b[0],b[1],b[2]+.045]  # leader points to the open rim
        ring_z = b_anchor[2]
        ring_radius = .058
        extra.append(dict(kind="pour_pose",point=list(spec["pouring_pose"])))
        note = (f"B = receiving cup centre; source pours above it "
                f"at z={spec['pouring_pose'][2]:.3f} m.")
    return dict(version="task_landmarks_v1",task_id=task,title=title,
        A=a,B=b,B_anchor=b_anchor,B_label=b_label,B_ring_z=ring_z,
        B_ring_radius_m=ring_radius,extras=extra,note=note,
        source="frozen task definition only; no runtime object truth",
        arrow_meaning="A to B task direction, NOT a robot trajectory",
        rendering="screen-space overlay only; no physics bodies or debug constraints")


def project_points(points, view, projection, size):
    """OpenGL column-major matrices -> image pixels, plus front-plane mask."""
    points = np.asarray(points,dtype=float).reshape(-1,3)
    world = np.column_stack((points,np.ones(len(points))))
    matrix = (np.asarray(projection).reshape(4,4,order="F") @
              np.asarray(view).reshape(4,4,order="F"))
    clip = world@matrix.T
    valid = clip[:,3] > 1e-8
    denominator = np.where(valid,clip[:,3],1)
    ndc = clip[:,:3]/denominator[:,None]
    width,height = size
    pixels = np.column_stack(((ndc[:,0]+1)*width/2,(1-ndc[:,1])*height/2))
    valid &= (ndc[:,2] >= -1)&(ndc[:,2] <= 1)
    return pixels,valid


@lru_cache(maxsize=8)
def _font(size):
    for name in ("arial.ttf","DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name,size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_annotations(image, annotations, view, projection, stage):
    """Draw projected rings/height guides, labels, direction arrow and legend."""
    if annotations is None:
        return image
    draw = ImageDraw.Draw(image)
    size = image.size
    def project(points):
        return project_points(points,view,projection,size)
    def line(points,color,width=2,dashed=False):
        pixels,valid = project(points)
        for i in range(len(pixels)-1):
            if not (valid[i] and valid[i+1]) or (dashed and i%2): continue
            pair = [tuple(pixels[i]),tuple(pixels[i+1])]
            draw.line(pair,fill=OUTLINE,width=width+2)
            draw.line(pair,fill=color,width=width)
    def ring(center,radius,color,dashed=False):
        angles = np.linspace(0,2*math.pi,49)
        points = [(center[0]+radius*math.cos(t),center[1]+radius*math.sin(t),center[2])
                  for t in angles]
        line(points,color,2,dashed)
    def vertical(x,y,z,color):
        line([(x,y,h) for h in np.linspace(.602,z,17)],color,1,True)
    def tag(point,text,color,offset):
        pixels,valid = project([point])
        if not valid[0]: return
        x,y = pixels[0]
        font = _font(14)
        bounds = draw.textbbox((0,0),text,font=font)
        w,h = bounds[2]-bounds[0]+14,bounds[3]-bounds[1]+12
        tx = max(5,min(size[0]-w-5,x+offset[0]))
        ty = max(77,min(size[1]-h-8,y+offset[1]))
        endpoint = (max(tx,min(tx+w,x)),max(ty,min(ty+h,y)))
        draw.line([(x,y),endpoint],fill=OUTLINE,width=4)
        draw.line([(x,y),endpoint],fill=color,width=2)
        draw.ellipse((x-4,y-4,x+4,y+4),fill=color,outline=OUTLINE,width=1)
        draw.rounded_rectangle((tx,ty,tx+w,ty+h),radius=5,fill=OUTLINE,outline=color,width=2)
        draw.text((tx+7,ty+5-bounds[1]),text,font=font,fill=color)
    a,b = annotations["A"],annotations["B"]
    # On-table footprints are fixed, including after the cup has moved away.
    ring((a[0],a[1],.602),.045,ORANGE)
    vertical(*a,ORANGE)
    ring((b[0],b[1],annotations["B_ring_z"]),annotations["B_ring_radius_m"],GREEN)
    if b[2] > .69:
        ring((b[0],b[1],.602),.025,GREEN,True)
    vertical(b[0],b[1],annotations["B_anchor"][2],GREEN)
    # Offset the tabletop direction guide so the two landmarks stay legible.
    start = (a[0]+.075,a[1],.604)
    end = (b[0]+.075,b[1],.604)
    points = [(1-f)*np.asarray(start)+f*np.asarray(end) for f in np.linspace(0,1,17)]
    line(points,(235,243,250),2,True)
    pixels,valid = project([start,end])
    if valid.all():
        tip = pixels[1]
        direction = tip-pixels[0]
        norm = np.linalg.norm(direction)
        if norm > 1:
            unit = direction/norm
            side = np.array([-unit[1],unit[0]])
            draw.polygon([tuple(tip),tuple(tip-10*unit+5*side),tuple(tip-10*unit-5*side)],
                         fill=(235,243,250))
            mid = (pixels[0]+tip)/2
            draw.text((mid[0]+8,mid[1]),"A > B",font=_font(12),fill="white",
                      stroke_width=2,stroke_fill=OUTLINE)
    for extra in annotations["extras"]:
        point = extra["point"]
        if extra["kind"] == "handoff":
            ring(point,.025,BLUE,True)
            line([point,b],BLUE,1,True)
            tag(point,"HANDOFF",BLUE,(70,28))
        elif extra["kind"] == "pour_pose":
            # Work height is separate from B, which is the receiving vessel.
            ring(point,.018,BLUE,True)
            tag(point,"POUR POSE",BLUE,(-125,-30))
    tag(a,"A START",ORANGE,(-108,30))
    tag(annotations["B_anchor"],annotations["B_label"],GREEN,(32,-17))
    draw.rectangle((0,0,size[0],72),fill=OUTLINE)
    draw.text((12,7),f"{annotations['title']}  |  {stage}",font=_font(15),fill="white")
    fmt = lambda point: "("+", ".join(f"{x:.3f}" for x in point)+")"
    draw.text((12,29),"A START "+fmt(a),font=_font(12),fill=ORANGE)
    draw.text((302,29),"B GOAL "+fmt(b),font=_font(12),fill=GREEN)
    draw.text((12,49),annotations["note"],font=_font(11),fill=(205,218,230))
    return image
