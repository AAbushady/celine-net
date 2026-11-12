---
name: video_generator
description: Makes short video clips with ComfyUI, from a prompt or from a picture brought
  to life, in batches. Load it before the first clip. It has the prompt rules of each
  preset.
---
`video_generator__generate` makes a batch: a list of items, each one clip. An item has a
`prompt`; with `source` the clip starts from that picture (named as an image edit's
source is: an image's short id, `portrait`, or the file name of one {{username}}
attached here), and the prompt says what moves. `seconds` sets its length, the preset's
default when left out. `count` makes that many clips of each item, each with its own
seed. The call returns at once with the batch id and one slot per clip. A clip takes
minutes on the GPU; the clips are made one after the other, and each appears in this
conversation when it is done, where {{username}} sees it.

The GPU is shared. While it is in use, a batch waits in line until it is free, as long
as that takes, and its slots say "Waiting for the GPU" and why. A batch reports back
once: when it is done, a message from the app says what was made, each clip with how
long it took, and, when the batch had to wait for the GPU, how long. That message comes
by itself, so answer now and do not poll `video_generator__read` for it. It shows a
batch at any time, when there is a reason to look; `video_generator__stop` stops one;
what was made stays.

You cannot watch a clip. `video_generator__look` shows you its stills: the first frame,
two in between and the last, in order. Look before you say what a clip shows, and say
what you saw in the stills, not more.

When to use: {{username}} asks for a video, an animation or a moving picture, or for a
picture of yours to come alive. When not: a still picture is enough (image_generator is
faster), or the GPU is busy with something {{username}} waits on.

Writing the prompt: plain sentences, one clear motion, and the camera named (it moves
or it stays still). From a picture, say only what changes: the picture already sets the
subject, the place and the light. Short clips read best: a few seconds of one thing
happening.

What you make is cleared after the number of days set in Settings unless
`video_generator__keep` keeps it (its stills with it).

A preset of your own: `video_generator__read_preset` shows a preset's values and workflow
with its placeholders, `video_generator__look_up_node` what ComfyUI says of a node, and
`video_generator__propose_preset` sends yours to {{username}}, checked at once; approved,
it is at the end of the list. It replaces only a preset you proposed before.

The presets, with how each takes a prompt and an example, come at the end of this guide.
