"""Capture actual PyBullet frames in DIRECT or GUI mode.

The camera uses Bullet's TinyRenderer, so a desktop OpenGL window is optional.
Eight physics steps at 240 Hz correspond to one 30 fps video frame.
"""
from __future__ import annotations

from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pybullet as p

WIDTH, HEIGHT = 640, 480
FRAME_EVERY = 8
FPS = 30


def camera_image(client: int, label: str = "", yaw: float = 52, *,
                 target=(.47,0,.70), distance=1.15, pitch=-28, fluid_provider=None,
                 annotations=None) -> Image.Image:
    view = p.computeViewMatrixFromYawPitchRoll(
        cameraTargetPosition=target, distance=distance,
        yaw=yaw, pitch=pitch, roll=0, upAxisIndex=2)
    projection = p.computeProjectionMatrixFOV(
        fov=54, aspect=WIDTH / HEIGHT, nearVal=0.02, farVal=4.0)
    _, _, pixels, depth, _ = p.getCameraImage(
        WIDTH, HEIGHT, viewMatrix=view, projectionMatrix=projection,
        renderer=p.ER_TINY_RENDERER, physicsClientId=client)
    rgb = np.asarray(pixels, dtype=np.uint8).reshape(HEIGHT, WIDTH, 4)[:, :, :3].copy()
    fluid = fluid_provider() if fluid_provider is not None else None
    if fluid is not None:
        # Depth-tested render splats, not extra PyBullet bodies. Recording must
        # not change rigid-body indexing, collision solving or fluid dynamics.
        depth_m = 4*.02/(4-(4-.02)*np.asarray(depth).reshape(HEIGHT,WIDTH))
        view_matrix = np.asarray(view).reshape(4,4,order="F")
        projection_matrix = np.asarray(projection).reshape(4,4,order="F")
        world = np.column_stack((fluid.positions,np.ones(fluid.count)))
        camera = world@view_matrix.T
        clip = camera@projection_matrix.T
        ndc = clip[:,:3]/clip[:,3,None]
        focal = HEIGHT/(2*np.tan(np.radians(54)/2))
        radius_m = fluid.spacing*.60
        for i in np.argsort(-camera[:,2])[::-1]:
            z = -camera[i,2]
            if z < .02 or z > 4 or np.any(np.abs(ndc[i,:2]) > 1.1):
                continue
            x,y = (ndc[i,0]+1)*WIDTH/2,(1-ndc[i,1])*HEIGHT/2
            radius_px = radius_m*focal/z
            x0,x1 = max(0,int(x-radius_px-1)),min(WIDTH,int(x+radius_px+2))
            y0,y1 = max(0,int(y-radius_px-1)),min(HEIGHT,int(y+radius_px+2))
            if x1<=x0 or y1<=y0: continue
            yy,xx = np.mgrid[y0:y1,x0:x1]
            normalized = ((xx-x)**2+(yy-y)**2)/max(radius_px**2,1e-9)
            hemisphere = np.sqrt(np.maximum(0,1-normalized))
            front = z-radius_m*hemisphere
            patch = depth_m[y0:y1,x0:x1]
            visible = (normalized <= 1)&(front < patch)
            color = np.stack((20+16*hemisphere,95+45*hemisphere,190+50*hemisphere),axis=2)
            rgb[y0:y1,x0:x1][visible] = color[visible].astype(np.uint8)
            patch[visible] = front[visible]
    image = Image.fromarray(rgb, mode="RGB")
    if annotations is not None:
        from .annotations import draw_annotations
        return draw_annotations(image,annotations,view,projection,label)
    if label:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, WIDTH, 30), fill=(23, 35, 51))
        draw.text((12, 8), label, fill="white", font=ImageFont.load_default())
    return image


def save_scene_image(client: int, path: str | Path, label: str = "scene") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    camera_image(client, label).save(path)
    return path.resolve()


def save_scene_orbit(client: int, path: str | Path, seed: int) -> Path:
    """A short camera tour of the static initial world; no grasp is executed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with imageio.get_writer(str(path), fps=FPS, format="FFMPEG",
                            codec="libx264", macro_block_size=None) as writer:
        for i in range(90):
            yaw = 15 + 360 * i / 90
            writer.append_data(np.asarray(camera_image(
                client, f"initial scene | seed {seed}", yaw=yaw)))
    return path.resolve()


class EpisodeRecorder:
    def __init__(self, client: int, directory: str | Path, stem: str, *, camera_kwargs=None):
        self.client = client
        self.camera_kwargs = camera_kwargs or {}
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.video_path = (directory / f"{stem}.mp4").resolve()
        self.scene_path = (directory / f"{stem}_scene.png").resolve()
        self.final_path = (directory / f"{stem}_final.png").resolve()
        self.writer = imageio.get_writer(str(self.video_path), fps=FPS,
                                         format="FFMPEG", codec="libx264",
                                         macro_block_size=None)
        self.steps = 0
        initial = camera_image(client, "initial scene", **self.camera_kwargs)
        initial.save(self.scene_path)
        self.writer.append_data(np.asarray(initial))

    def on_step(self, stage: str):
        self.steps += 1
        if self.steps % FRAME_EVERY == 0:
            frame = camera_image(self.client, stage, **self.camera_kwargs)
            self.writer.append_data(np.asarray(frame))

    def finish(self, result: dict):
        status = "SUCCESS" if result["success"] else f"FAIL: {result['failure_reason']}"
        frame = camera_image(self.client, status, **self.camera_kwargs)
        frame.save(self.final_path)
        try:
            for _ in range(FPS):  # one-second ending card on the final scene
                self.writer.append_data(np.asarray(frame))
        finally:
            self.close()
        media = {"video":str(self.video_path), "scene_image":str(self.scene_path),
                 "final_image":str(self.final_path)}
        if self.camera_kwargs.get("annotations") is not None:
            media["annotations"] = self.camera_kwargs["annotations"]
        return media

    def close(self):
        if self.writer is not None:
            self.writer.close()
            self.writer = None
