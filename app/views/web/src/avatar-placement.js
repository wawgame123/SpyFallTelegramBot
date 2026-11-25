export const limits = {arc: 65, radius: 42, rotation: 180, minScale: 50, maxScale: 150};

const defaults = {
  hat: {arc: 0, rotation: 0, scale: 100, mirror: false},
  face: {x: 0, y: 14, rotation: 0, scale: 100, mirror: false},
};

export function poseFor(look, slot) {
  return {...defaults[slot], ...look?.placement?.[slot]};
}

export function constrainFace(x, y) {
  const distance = Math.hypot(x, y);
  if (distance <= limits.radius) return {x, y};
  const ratio = limits.radius / distance;
  return {x: Math.trunc(x * ratio), y: Math.trunc(y * ratio)};
}

export const clampArc = (angle) => Math.min(limits.arc, Math.max(-limits.arc, angle));
const radians = (degrees) => degrees * Math.PI / 180;

export function accessoryGeometry(slot, pose) {
  const scale = pose.scale / 100;
  const width = (slot === "hat" ? 0.79 : 0.78) * scale;
  const height = (slot === "hat" ? 0.46 : 0.43) * scale;
  const rotation = pose.rotation + (pose.arc || 0);
  let x = 0.5 + (pose.x || 0) / 100;
  let y = 0.5 + (pose.y || 0) / 100;

  if (slot === "hat") {
    const arc = radians(pose.arc);
    const roll = radians(pose.rotation);
    // Keep the entire rotated rectangle outside a tangent to the photo.
    const depth = (Math.abs(Math.sin(roll)) * width + Math.abs(Math.cos(roll)) * height) / 2;
    const radius = 0.51 + depth;
    x = 0.5 + Math.sin(arc) * radius;
    y = 0.5 - Math.cos(arc) * radius;
  }

  const angle = radians(rotation);
  const halfWidth = (Math.abs(Math.cos(angle)) * width + Math.abs(Math.sin(angle)) * height) / 2;
  const halfHeight = (Math.abs(Math.sin(angle)) * width + Math.abs(Math.cos(angle)) * height) / 2;
  return {
    style: {
      left: x * 100 + "%", top: y * 100 + "%", width: width * 100 + "%", height: height * 100 + "%",
      transform: "translate(-50%, -50%) rotate(" + rotation + "deg) scaleX(" + (pose.mirror ? -1 : 1) + ")",
    },
    reserve: {
      "--hat-top": Math.max(0, halfHeight - y),
      "--hat-right": Math.max(0, x + halfWidth - 1),
      "--hat-bottom": Math.max(0, y + halfHeight - 1),
      "--hat-left": Math.max(0, halfWidth - x),
    },
  };
}
