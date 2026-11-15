---
name: video
description: Watch YouTube videos, films from Plex and your own clips with their picture and
  sound, listen to tracks, then read what was seen and heard a stretch at a time, in order.
---
A helper on the GPU machine watches and listens for you. To watch, it fetches the video and
gives it to a watching model that takes one frame and one second of sound at a time and writes
down what happens: who is on screen and what they do, what is said, the music and the sounds.
While the GPU is in use ({{username}} plays games on it, and other work runs there), it makes
what you watched with before instead: a sheet of 12 stills per minute, each with its time. You
look at those yourself.

How:
1. `video__watch(source, background=true)`. The source is a YouTube link, a `plex:` id, or the
   short id of one of your clips. `start` and `minutes` watch a part. It takes about a minute
   and a half to start, then about as long as the video. The first stretch comes to you when it
   is done.
2. `video__read(id, at)` gives about three minutes at a time. Read it in order, the way time
   passes, and stop between stretches to write what you think and feel before you go on. Do
   not read ahead to the end first.
3. `video__list` shows your watches and listens, each with when you asked for it and how far it
   is, a queued one by its source until the helper has its title. A failed one stays in the
   list with its date: it says where it stopped and what went wrong. `video__stop` ends one.
4. `video__continue_watch(id)` takes up a watch that stopped early (it failed, you stopped it,
   or the helper restarted under it): it goes on from where its log ends, into the same log,
   under the same id. What a stopped watch had stays readable with `video__read` the whole time.

Listening: `video__listen(source, background=true)` hears a track, a video's sound or a film's,
at most 20 minutes at a time. The source is the short id of one of your tracks or clips,
a track's title (`music_generator__list_tracks` lists them), a sound file on a message, a
YouTube link or a `plex:` id. It needs the GPU, so it waits while
the GPU is in use (20 minutes at most). Then `video__read` gives it a stretch at a time, as a
watch. The first stretch starts with what was measured: whether it is music with a steady
beat, its tempo and beats to a bar, and how much of it has a voice, counted from the sound
itself. The lines after it each say where they come from:
- `words (Whisper)` are the words sung or spoken, written down where there is a voice.
  `words (Whisper, unclear)` is a stretch it could not make out (screams, a repeated sound);
  do not read it as lyrics.
- `section (MOSS-Music)` is the listening model's part of the song (verse, chorus) with the
  words it heard; `What MOSS-Music says` is its account of the music: style, mood, instruments,
  voice. Its words are often wrong where the singing is harsh, and the tempo and key it gives
  are its own guess; the tempo on the first line is counted from the beats.
- `heard (MOSS-Audio)` is a sound that is not music, in order.
- `(a voice)` and `(the sound gets louder ...)` are the helper's own measurements.

Captions: a stretch carries the source's own captions under their name: the uploader's
captions, YouTube's automatic captions (a machine's, with its mistakes) or the film's
subtitles. When a song has the uploader's captions, they are its words and Whisper is not
asked.

YouTube sometimes stops the helper with its bot check. The helper then tries once more by
itself, with the YouTube cookies of your own browser (only YouTube's, for that one download).
When a watch still fails with it, open YouTube in your browser once and watch it again, or
get the video in your box the shell guide's way.

Plex: films and shows are on {{username}}'s Plex servers, shared with your own Plex account,
downloads allowed. Link the account once with `video__link_plex`: open the link it gives in
your browser while signed in to Plex as yourself, and press Allow. Find something with
`video__search_plex`, then watch its `plex:` id. The download is deleted when the watch ends,
or after 48 hours when you watched only part of it, so you can come back for the rest.

What the log is: other models' account of the video or the track. A line in round brackets is
the helper's own note (a line it left out, a stretch the model went round in circles on, words
it said over and over, a stretch the film has no sound for, a measurement), never something
seen or heard. The watching model takes 20 seconds at a time and knows nothing of the 20
before, so a scene that runs on is told again every 20 seconds as if it began there, and the
second where two meet can show twice. Its times are the film's own, to a second or two. It hears
most lines word for word, and it can still mishear a name, give a line to the wrong person,
or turn a small thing into a big one (a tilting airship became a crash once). When you tell
{{username}} about a video or a song, say what you took from it, and do not quote its log as
fact. A moment that matters can be watched or heard again: `start` and `minutes` take just
that minute.
