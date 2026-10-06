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


def camera_image(client: int, label: str = "", yaw: float = 52) -> Image.Image:
    view = p.computeViewMatrixFromYawPitchRoll(
        cameraTargetPosition=[0.47, 0.0, 0.70], distance=1.15,
        yaw=yaw, pitch=-28, roll=0, upAxisIndex=2)
    projection = p.computeProjectionMatrixFOV(
        fov=54, aspect=WIDTH / HEIGHT, nearVal=0.02, farVal=4.0)
    _, _, pixels, _, _ = p.getCameraImage(
        WIDTH, HEIGHT, viewMatrix=view, projectionMatrix=projection,
        renderer=p.ER_TINY_RENDERER, physicsClientId=client)
    rgb = np.asarray(pixels, dtype=np.uint8).reshape(HEIGHT, WIDTH, 4)[:, :, :3]
    image = Image.fromarray(rgb, mode="RGB")
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
    def __init__(self, client: int, directory: str | Path, stem: str):
        self.client = client
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.video_path = (directory / f"{stem}.mp4").resolve()
        self.scene_path = (directory / f"{stem}_scene.png").resolve()
        self.final_path = (directory / f"{stem}_final.png").resolve()
        self.writer = imageio.get_writer(str(self.video_path), fps=FPS,
                                         format="FFMPEG", codec="libx264",
                                         macro_block_size=None)
        self.steps = 0
        initial = camera_image(client, "initial scene")
        initial.save(self.scene_path)
        self.writer.append_data(np.asarray(initial))

    def on_step(self, stage: str):
        self.steps += 1
        if self.steps % FRAME_EVERY == 0:
            frame = camera_image(self.client, stage)
            self.writer.append_data(np.asarray(frame))

    def finish(self, result: dict):
        status = "SUCCESS" if result["success"] else f"FAIL: {result['failure_reason']}"
        frame = camera_image(self.client, status)
        frame.save(self.final_path)
        try:
            for _ in range(FPS):  # one-second ending card on the final scene
                self.writer.append_data(np.asarray(frame))
        finally:
            self.close()
        return {"video":str(self.video_path), "scene_image":str(self.scene_path),
                "final_image":str(self.final_path)}

    def close(self):
        if self.writer is not None:
            self.writer.close()
            self.writer = None
