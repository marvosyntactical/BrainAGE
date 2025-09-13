import os

path = "/media/silversurfer42/Sandisk P/neuro/frailty/data/for_brainage/"

wm_dir = "rp1_CAT12.9"

# groups = ["D","F","K","FD"]
groups = ["D", "F", "K"]

for g in groups:
    seg_dir = os.path.join(path, g, wm_dir)
    print(g, len(os.listdir(seg_dir)))


