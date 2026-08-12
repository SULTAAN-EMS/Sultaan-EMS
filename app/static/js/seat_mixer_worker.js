/*
 * Seat Mixer optimization worker.
 *
 * One objective drives Quick Generate, Scatter, and Strict Optimizer.  This
 * keeps the visible integrity statistics faithful to the arrangement that was
 * actually produced, rather than letting each button optimize a different
 * approximation of examination integrity.
 */
(function () {
  'use strict';

  function distance(a, b) {
    // A seat offset makes two seats at the same table physically closer than
    // seats at neighbouring tables, while preserving the hall grid geometry.
    return Math.abs(a.row - b.row) + Math.abs(a.tableIdx - b.tableIdx) +
      (a.tableIdx === b.tableIdx && a.row === b.row ? Math.abs(a.seatIdx - b.seatIdx) * 0.15 : 0);
  }

  function pairPenalty(a, b) {
    if (!a.assigned || !b.assigned || a.assigned.classId !== b.assigned.classId) return 0;
    var d = distance(a, b);
    if (d < 0.5) return 100000;       // same physical table: hard violation
    if (d <= 1.05) return 8500;       // adjacent table / immediate neighbour
    if (d <= 2.05) return 1700;
    if (d <= 3.05) return 320;
    return Math.max(0, 55 - d * 5);   // retain a gentle distance incentive
  }

  function score(seats) {
    var total = 0;
    for (var i = 0; i < seats.length; i++) {
      for (var j = i + 1; j < seats.length; j++) total += pairPenalty(seats[i], seats[j]);
    }
    return total;
  }

  function metrics(seats) {
    var hard = 0, adjacent = 0, near = 0, sameRow = 0, sameClassPairs = 0, distanceSum = 0;
    for (var i = 0; i < seats.length; i++) {
      for (var j = i + 1; j < seats.length; j++) {
        var a = seats[i], b = seats[j];
        if (!a.assigned || !b.assigned || a.assigned.classId !== b.assigned.classId) continue;
        sameClassPairs++;
        var d = distance(a, b);
        distanceSum += d;
        if (d < 0.5) hard++;
        else if (d <= 1.05) adjacent++;
        else if (d <= 2.05) near++;
        if (a.row === b.row && d > 1.05 && d <= 3.05) sameRow++;
      }
    }
    // A normalized operational score: hard violations dominate, while the
    // unavoidable near pairs in a full hall remain visible without forcing a
    // mathematically sound layout to read as a failure.
    var integrity = Math.max(0, Math.round(100 - hard * 25 - adjacent * 3 - near * 0.25 - sameRow * 0.1));
    return {
      hardCount: hard,
      softCount: adjacent,
      nearCount: near,
      sameRowCount: sameRow,
      avgSameClassDistance: sameClassPairs ? +(distanceSum / sameClassPairs).toFixed(2) : 0,
      integrityScore: integrity,
      cost: score(seats)
    };
  }

  function shuffled(items) {
    var copy = items.slice();
    for (var i = copy.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = copy[i]; copy[i] = copy[j]; copy[j] = tmp;
    }
    return copy;
  }

  function spreadSeatOrder(seats, varySeed) {
    // Farthest-point sampling: with spare capacity, occupied seats naturally
    // cover the whole hall before the engine uses close neighbours.
    var remaining = seats.slice();
    var out = [];
    if (!remaining.length) return out;
    var centerRow = (Math.max.apply(null, remaining.map(function (s) { return s.row; })) || 0) / 2;
    var centerTable = (Math.max.apply(null, remaining.map(function (s) { return s.tableIdx; })) || 0) / 2;
    remaining.sort(function (a, b) {
      return (Math.abs(b.row - centerRow) + Math.abs(b.tableIdx - centerTable)) -
        (Math.abs(a.row - centerRow) + Math.abs(a.tableIdx - centerTable));
    });
    if (varySeed) {
      // Strict uses different, still hall-wide starting points for every
      // restart. Picking from the outer third retains real empty-seat spread
      // without repeatedly walking the same symmetric layout.
      var seedWindow = Math.max(1, Math.ceil(remaining.length / 3));
      out.push(remaining.splice(Math.floor(Math.random() * seedWindow), 1)[0]);
    } else out.push(remaining.shift());
    while (remaining.length) {
      var bestIndex = 0, bestDistance = -1, contenders = [];
      for (var i = 0; i < remaining.length; i++) {
        var nearest = Infinity;
        for (var j = 0; j < out.length; j++) nearest = Math.min(nearest, distance(remaining[i], out[j]));
        if (nearest > bestDistance) {
          bestDistance = nearest;
          bestIndex = i;
          contenders = [i];
        } else if (varySeed && nearest >= bestDistance - 0.35) contenders.push(i);
      }
      if (varySeed && contenders.length) bestIndex = contenders[Math.floor(Math.random() * contenders.length)];
      out.push(remaining.splice(bestIndex, 1)[0]);
    }
    return out;
  }

  function initialPlacement(seats, students, scatter, varySeed) {
    seats.forEach(function (seat) { seat.assigned = null; });
    var pools = {};
    students.forEach(function (student) { (pools[student.classId] = pools[student.classId] || []).push(student); });
    Object.keys(pools).forEach(function (key) { pools[key] = shuffled(pools[key]); });
    var order = spreadSeatOrder(seats, varySeed);
    // Quick fills a broadly distributed order; Scatter uses the same optimal
    // spacing order but gives class balance a stronger tie-break preference.
    var placed = [];
    order.forEach(function (seat) {
      var classes = Object.keys(pools).filter(function (key) { return pools[key].length; });
      if (!classes.length) return;
      classes = shuffled(classes);
      var best = classes[0], bestScore = Infinity;
      classes.forEach(function (classId) {
        var candidate = { row: seat.row, tableIdx: seat.tableIdx, seatIdx: seat.seatIdx, assigned: { classId: parseInt(classId, 10) } };
        var penalty = 0;
        for (var i = 0; i < placed.length; i++) penalty += pairPenalty(candidate, placed[i]);
        var balance = pools[classId].length * (scatter ? 0.13 : 0.06);
        var scoreHere = penalty - balance;
        if (scoreHere < bestScore) { bestScore = scoreHere; best = classId; }
      });
      var student = pools[best].shift();
      seat.assigned = student;
      student.seatId = seat.id;
      placed.push(seat);
    });
    return seats;
  }

  function contribution(seats, index) {
    var value = 0;
    for (var i = 0; i < seats.length; i++) if (i !== index) value += pairPenalty(seats[index], seats[i]);
    return value;
  }

  function optimiseRun(seats, iterations) {
    var occupied = seats.filter(function (seat) { return seat.assigned; });
    if (occupied.length < 2) return score(seats);
    var bestScore = score(seats), currentScore = bestScore;
    var best = seats.map(function (seat) { return seat.assigned; });
    var temperature = Math.max(75, bestScore * 0.018);
    var cooling = Math.pow(0.002, 1 / Math.max(iterations, 1));
    var stagnant = 0;
    for (var step = 0; step < iterations && stagnant < Math.max(900, iterations * 0.22); step++) {
      temperature *= cooling;
      var aIndex = Math.floor(Math.random() * seats.length);
      var bIndex = Math.floor(Math.random() * seats.length);
      if (aIndex === bIndex || !seats[aIndex].assigned) continue;
      var a = seats[aIndex], b = seats[bIndex];
      if (b.assigned && a.assigned.classId === b.assigned.classId) continue;
      var before = contribution(seats, aIndex) + contribution(seats, bIndex) - pairPenalty(a, b);
      var left = a.assigned, right = b.assigned;
      a.assigned = right; b.assigned = left;
      var after = contribution(seats, aIndex) + contribution(seats, bIndex) - pairPenalty(a, b);
      var delta = after - before;
      if (delta <= 0 || Math.random() < Math.exp(-delta / Math.max(temperature, 0.1))) {
        currentScore += delta;
        if (currentScore < bestScore) {
          bestScore = currentScore;
          best = seats.map(function (seat) { return seat.assigned; });
          stagnant = 0;
        } else stagnant++;
      } else {
        a.assigned = left; b.assigned = right;
        stagnant++;
      }
    }
    seats.forEach(function (seat, index) { seat.assigned = best[index]; if (seat.assigned) seat.assigned.seatId = seat.id; });
    return bestScore;
  }

  function isBetterMetrics(candidate, current) {
    // These checks deliberately mirror the integrity policy shown in the UI.
    // A prettier average distance can never compensate for a hard violation.
    var lowerIsBetter = ['hardCount', 'softCount', 'nearCount', 'sameRowCount'];
    for (var i = 0; i < lowerIsBetter.length; i++) {
      var key = lowerIsBetter[i];
      if (candidate[key] !== current[key]) return candidate[key] < current[key];
    }
    if (candidate.avgSameClassDistance !== current.avgSameClassDistance) {
      return candidate.avgSameClassDistance > current.avgSameClassDistance;
    }
    return candidate.cost < current.cost;
  }

  function hasSameMetrics(left, right) {
    return left.hardCount === right.hardCount &&
      left.softCount === right.softCount &&
      left.nearCount === right.nearCount &&
      left.sameRowCount === right.sameRowCount &&
      left.avgSameClassDistance === right.avgSameClassDistance &&
      left.cost === right.cost;
  }

  function assignmentsDiffer(seats, assignments) {
    return seats.some(function (seat, index) {
      var currentId = seat.assigned ? seat.assigned.id : null;
      var savedId = assignments[index] ? assignments[index].id : null;
      return currentId !== savedId;
    });
  }

  function restoreAssignments(seats, assignments) {
    seats.forEach(function (seat, index) {
      seat.assigned = assignments[index] || null;
      if (seat.assigned) seat.assigned.seatId = seat.id;
    });
  }

  function strictOptimise(seats, students, iterations) {
    var startedAt = Date.now();
    var requestedIterations = Math.max(6000, iterations || 12000);
    // Strict intentionally has a larger search budget than Quick. The time
    // guard keeps the worker responsive for the largest real examination halls.
    var timeBudgetMs = Math.min(4800, Math.max(2200, Math.round(requestedIterations * 0.28)));
    var restartTarget = Math.max(24, Math.min(72, Math.max(
      Math.ceil(students.length * 1.25),
      Math.floor(requestedIterations / 300)
    )));
    var stepsPerRestart = Math.max(420, Math.min(1400, Math.floor(requestedIterations / restartTarget)));
    var hadExistingLayout = seats.some(function (seat) { return seat.assigned; });
    var original = seats.map(function (seat) { return seat.assigned; });
    var originalMetrics = hadExistingLayout ? metrics(seats) : null;
    var best = original.slice();
    var bestMetrics = originalMetrics || null;
    var attempts = 0;

    // Always build fresh candidates. This makes a second Strict click a real
    // search from the current data, rather than a no-op on a previous optimum.
    while (attempts < restartTarget && Date.now() - startedAt < timeBudgetMs) {
      initialPlacement(seats, students, true, true);
      optimiseRun(seats, stepsPerRestart);
      var candidateMetrics = metrics(seats);
      if (!bestMetrics || isBetterMetrics(candidateMetrics, bestMetrics) ||
          (hasSameMetrics(candidateMetrics, bestMetrics) && assignmentsDiffer(seats, best))) {
        best = seats.map(function (seat) { return seat.assigned; });
        bestMetrics = candidateMetrics;
      }
      attempts++;
    }

    // Preserve the existing layout only when it is objectively better. A strict
    // run therefore never regresses a manually improved arrangement.
    if (originalMetrics && isBetterMetrics(originalMetrics, bestMetrics)) {
      best = original;
      bestMetrics = originalMetrics;
    }
    restoreAssignments(seats, best);
    return {
      seats: seats,
      metrics: bestMetrics || metrics(seats),
      attempts: attempts,
      durationMs: Date.now() - startedAt
    };
  }

  self.onmessage = function (event) {
    var msg = event.data || {};
    var seats = msg.seats || [];
    var students = msg.students || [];
    if (msg.type === 'quick') {
      seats = initialPlacement(seats, students, false);
      optimiseRun(seats, Math.max(1800, Math.min(4500, students.length * 140)));
    }
    else if (msg.type === 'scatter') {
      seats = initialPlacement(seats, students, true);
      optimiseRun(seats, Math.max(3000, Math.min(6500, students.length * 220)));
    }
    var optimisation = null;
    if (msg.type === 'strict') {
      optimisation = strictOptimise(seats, students, msg.iterations || 12000);
      seats = optimisation.seats;
    }
    else if (msg.type !== 'quick' && msg.type !== 'scatter') return;
    self.postMessage({
      type: msg.type + '_done',
      runId: msg.runId,
      seats: seats,
      metrics: optimisation ? optimisation.metrics : metrics(seats),
      optimisation: optimisation ? { attempts: optimisation.attempts, durationMs: optimisation.durationMs } : null
    });
  };
})();
