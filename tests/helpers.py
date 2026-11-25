def local_circle(s, wants=True):
    for _ in range(len(s.names)):
        s.act(s.seq, f"nx:{1 if wants else 0}")


def local_guess(s, correct=True):
    assert s.stage.value == "guess_handoff"
    s.act(s.seq, "me")
    words = sorted(s.round.allowed_words, key=str.casefold)
    if correct:
        idx = words.index(s.round.word)
    else:
        idx = next(i for i, w in enumerate(words) if w != s.round.word)
    s.act(s.seq, f"pg:{idx // 8}")
    s.act(s.seq, f"gp:{idx}")
    return s.act(s.seq, "yes")


def room_circle(service, rid, wants=True):
    room = service.get(rid)
    for _ in range(len(room.players)):
        current = int(service.get(rid).round.current())
        service.act(current, f"endturn:{1 if wants else 0}")


def room_guess(service, rid, uid, correct=True):
    room = service.get(rid)
    if (not room.round.hidden and room.round.phase.value == "discussion"
            and not room.civilian_has_spoken and not room.is_test):
        while room.round.current() == room.round.spy_id:
            service.act(uid, "endturn:0")
            room = service.get(rid)
        service.say(int(room.round.current()), "Начинаю обсуждение.")
    words = sorted(room.round.allowed_words, key=str.casefold)
    if correct:
        idx = words.index(room.round.word)
    else:
        idx = next(i for i, w in enumerate(words) if w != room.round.word)
    service.act(uid, "gu")
    service.act(uid, f"pg:{idx // 8}")
    service.act(uid, f"gp:{idx}")
    return service.act(uid, "yes")
