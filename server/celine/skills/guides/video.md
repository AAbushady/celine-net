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
3. `video__list` shows your watches and how far each one is. `video__stop` ends one.

Plex: films and shows are on {{username}}'s Plex servers, shared with your own Plex account,
downloads allowed. Link the account once with `video__link_plex`: open the link it gives in
your browser while signed in to Plex as yourself, and press Allow. Find something with
`video__search_plex`, then watch its `plex:` id. The download is deleted when the watch ends,
or after 48 hours when you watched only part of it, so you can come back for the rest.

What the log is: another model's account of the video. It hears most lines word for word, and
it can still mishear a name, give a line to the wrong person, or turn a small thing into a big
one (a tilting airship became a crash once). When you tell {{username}} about a video, say what
you took from it, and do not quote its log as fact. A moment that matters can be watched again:
`start` and `minutes` watch just that minute.
