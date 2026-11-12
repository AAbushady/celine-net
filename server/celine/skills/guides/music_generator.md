---
name: music_generator
description: Makes songs and instrumental tracks with ComfyUI, in batches. Use it when
  {{username}} asks for music, a track, an ambient soundscape or a song.
---
`music_generator__generate` makes a batch: a list of items, each one track. An item has
a `style`; a song also has `lyrics` and `instrumental=false`. `count` makes that many
tracks of each item, each with its own seed. The call returns at once with the batch
id and one slot per track. The tracks are made one after the other, a few minutes
each, and each appears in this conversation when it is done, where {{username}} hears
it. It is also saved in the music folder of the data folder. What you make is cleared
after the number of days set in Settings unless `music_generator__keep` keeps it.

The GPU is shared. While it is in use, a batch waits in line until it is free, as long
as that takes, and its slots say "Waiting for the GPU" and why. A batch reports back
once: when it is done, a message from the app says what was made and, when it had to
wait for the GPU, how long. That message comes by itself, so answer now and do not poll
`music_generator__read` for it. It shows
a batch at any time, when there is a reason to look; `music_generator__stop` stops one;
what was made stays. With `wait=true` your turn waits until the batch is done, as long
as your turn's time allows; past it, the batch goes on. A batch that still waits for
the GPU after a short try comes back as it is: tell {{username}} it is made when the
GPU is free, and answer.

When to use: {{username}} asks you to compose music, make an instrumental or an
ambient background, or write a song.

A preset of your own: `music_generator__read_preset` shows a preset's values and workflow
with its placeholders, `music_generator__look_up_node` what ComfyUI says of a node, and
`music_generator__propose_preset` sends yours to {{username}}, checked at once; approved,
it is at the end of the list. It replaces only a preset you proposed before.

The preset, with how it takes a style and an example, comes at the end of this guide.
