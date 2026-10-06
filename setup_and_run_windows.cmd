@echo off
setlocal
cd /d "%~dp0"

where conda >nul 2>&1
if errorlevel 1 (
  echo Conda was not found. Open Miniforge Prompt or Anaconda Prompt, then run this file there.
  goto :failed
)

echo [1/4] Checking the grasp-a environment...
conda run -n grasp-a python -V >nul 2>&1
if errorlevel 1 (
  conda env create -f environment.yml
  if errorlevel 1 goto :failed
)

echo [2/4] Checking simulation and video dependencies...
conda run -n grasp-a python -c "import pybullet, pybullet_data, numpy, PIL, imageio, imageio_ffmpeg; print('PyBullet OK:', pybullet_data.getDataPath())"
if errorlevel 1 (
  echo The environment is incomplete. Run: conda env update -f environment.yml
  goto :failed
)

if not exist results\local mkdir results\local

echo [3/4] Checking input format and feasibility...
conda run -n grasp-a python -m grasp_a validate
if errorlevel 1 goto :failed
conda run -n grasp-a python -m grasp_a filter --seed 0 --out results/local/filter_seed0.json
if errorlevel 1 goto :failed

echo [4/4] Rendering a scene and recording one pipeline demo...
conda run -n grasp-a python -m grasp_a scene --seed 0 --out results/local/scene_seed0.png --video results/local/scene_orbit_seed0.mp4
if errorlevel 1 goto :failed
conda run -n grasp-a python -m grasp_a demo --task handover_prep --seed 0 --visualize --media-dir results/local/visuals --out results/local/handover_seed0.json
if errorlevel 1 goto :failed

echo Done. Check results\local\handover_seed0.json and results\local\visuals\.
pause
exit /b 0

:failed
echo Stopped at the first failure. Copy the full error output when asking for help.
pause
exit /b 1
