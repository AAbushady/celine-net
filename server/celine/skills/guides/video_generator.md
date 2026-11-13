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

`video_generator__look` shows you a clip: its four stills (the first frame, two in
between and the last), or with `frames` that many frames sampled evenly from the clip,
each with its time. Four stills show where a clip starts and ends, not how it moves: before
you say what moves, when, or for how long, look with frames (12 show a five-second clip
every half second). Say what the frames show, not more: between two frames you saw nothing.

`video_generator__add_sound(clip=..., prompt=...)` gives one of your clips its sound: the
picture stays as it was, and a sound preset makes what the prompt says is heard, timed to
what moves. The result is a new clip; the silent one stays, so another prompt can try
again. Write what is heard, not what is seen: the place's steady sound, each sound of what
moves when it happens, a voice only where one belongs, and "no music" when there is none
(the sound preset's own rules come at the end of this guide). A preset whose clips come
with their own sound needs none of this.

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

Another video model comes as a preset of yours whose workflow names its files: propose
it even when ComfyUI does not have them yet (the request says which). Once it is approved,
find each file with `image_generator__search_models` and `image_generator__read_model`,
and ask for it with `video_generator__request_model(preset=..., kind=..., file=...,
why=...)`, its `kind` the loader's (`model`, `lora`, `vae` or `text_encoder`). Each goes
where the workflow names it, after {{username}} approves; a message from the app says when
it is ready.

The presets, with how each takes a prompt and an example, come at the end of this guide.
