#version 330 core

in vec2 v_texcoord;
out vec4 fragColor;

uniform sampler2D u_texture;
uniform float u_intensity;    // 0-1, overall vignette strength
uniform float u_softness;     // how gradual the falloff is (e.g. 0.4-0.8)
uniform float u_radius;       // where the darkening begins (e.g. 0.5-0.9)

void main() {
    vec4 color = texture(u_texture, v_texcoord);

    // Distance from center of screen (0,0 at center, ~0.707 at corners)
    vec2 center_offset = v_texcoord - vec2(0.5, 0.5);
    float dist = length(center_offset);

    // Smooth vignette falloff
    float vignette = 1.0 - smoothstep(u_radius, u_radius + u_softness, dist);

    // Mix between original color and darkened version
    color.rgb *= mix(1.0, vignette, u_intensity);

    fragColor = color;
}
