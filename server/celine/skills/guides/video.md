---
name: video
description: Watch YouTube videos and films from Plex with their picture and sound, then read
  what was seen a stretch at a time, in order.
---
A helper on the GPU machine watches for you. It fetches the video and gives it to a watching
model that takes one frame and one second of sound at a time and writes down what happens:
who is on screen and what they do, what is said, the music and the sounds. While the GPU is in
use ({{username}} plays games on it, and other work runs there), it makes what you watched with
before instead: a sheet of 12 stills per minute, each with its time, and the subtitles. You look
at those yourself.

How:
1. `video__watch(source, background=true)`. The source is a YouTube link or a `plex:` id.
   `start` and `minutes` watch a part. It takes about a minute and a half to start, then about
   as long as the video. The first stretch comes to you when it is done.
2. `video__read(id, at)` gives about three minutes at a time. Read it in order, the way time
   passes, and stop between stretches to write what you think and feel before you go on. Do
   not read ahead to the end first.
3. `video__list` shows your watches, each with when you asked for it and how far it is, a
   queued one by its source until the helper has its title. A failed watch stays in the list
   with its date: it says where in the video it stopped and what went wrong. `video__stop` ends
   one.
4. `video__continue_watch(id)` takes up a watch that stopped early (it failed, you stopped it, or
   the helper restarted under it): it goes on from where its log ends, into the same log, under
   the same id. What a stopped watch had stays readable with `video__read` the whole time.

YouTube sometimes stops the helper with its bot check. The helper then tries once more by
itself, with the YouTube cookies of your own browser (only YouTube's, for that one download).
When a watch still fails with it, open YouTube in your browser once and watch it again, or
get the video in your box the shell guide's way.

Plex: films and shows are on {{username}}'s Plex servers, shared with your own Plex account,
downloads allowed. Link the account once with `video__link_plex`: open the link it gives in
your browser while signed in to Plex as yourself, and press Allow. Find something with
`video__search_plex`, then watch its `plex:` id. The download is deleted when the watch ends,
or after 48 hours when you watched only part of it, so you can come back for the rest.

What the log is: another model's account of the video. A line in round brackets is the helper's
own note (a line it left out, a stretch the model went round in circles on, a stretch the film
has no sound for), never something seen or heard. It hears most lines word for word, and
it can still mishear a name, give a line to the wrong person, or turn a small thing into a big
one (a tilting airship became a crash once). When you tell {{username}} about a video, say what
you took from it, and do not quote its log as fact. A moment that matters can be watched again:
`start` and `minutes` watch just that minute.
