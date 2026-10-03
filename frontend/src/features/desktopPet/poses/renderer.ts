import { manifest, poseArtRect, type Pose } from './contract'
import type { PoseMotion } from './motion'

// The same connected mesh and local-eye masks approved in the pose study.
// No separate limbs, whole-face blink replacement, or native-model fallback.
export class PoseRenderer {
  readonly canvas: HTMLCanvasElement
  private readonly gl: WebGLRenderingContext
  private readonly program: WebGLProgram
  private readonly buffer: WebGLBuffer
  private readonly textures = new Map<string, WebGLTexture>()
  private readonly uniforms: Record<string, WebGLUniformLocation | null>
  private readonly vertexCount: number
  private disposed = false

  constructor(images: Map<string, HTMLImageElement>) {
    this.canvas = document.createElement('canvas')
    this.canvas.width = manifest.width
    this.canvas.height = manifest.height
    const gl = this.canvas.getContext('webgl', {
      alpha: true, antialias: false, premultipliedAlpha: true, preserveDrawingBuffer: true,
    })
    if (!gl) throw new Error('无法建立桌宠图形上下文，请检查显卡或 WebView2。')
    this.gl = gl
    const vertex = `
      attribute vec2 position;
      uniform vec2 size;
      uniform vec3 movement;
      uniform vec2 anchors;
      uniform vec4 artRect;
      varying vec2 uv;
      varying vec2 artPoint;
      void main(){
        uv=(position-artRect.xy)/artRect.zw;
        artPoint=position;
        float upper=1.0-smoothstep(min(anchors.y-20.0,480.0),540.0,position.y);
        float head=1.0-smoothstep(anchors.x-8.0,anchors.x+85.0,position.y);
        vec2 p=position+vec2(movement.x*upper,movement.y*head+movement.z*upper);
        gl_Position=vec4(p.x/size.x*2.0-1.0,1.0-p.y/size.y*2.0,0.0,1.0);
      }`
    const fragment = `
      precision mediump float;
      varying vec2 uv;
      varying vec2 artPoint;
      uniform sampler2D baseArt;
      uniform sampler2D frameArt;
      uniform float localEyes;
      uniform vec4 eyeA;
      uniform vec4 eyeB;
      uniform vec2 eyeAngles;
      float eyeWeight(vec2 point, vec4 region, float angle) {
        vec2 d=point-region.xy;
        vec2 p=vec2(cos(angle)*d.x+sin(angle)*d.y,-sin(angle)*d.x+cos(angle)*d.y)/region.zw;
        return 1.0-smoothstep(0.94,1.0,max(abs(p.x),abs(p.y)));
      }
      void main(){
        if(any(lessThan(uv,vec2(0.0))) || any(greaterThan(uv,vec2(1.0)))) discard;
        vec4 original=texture2D(baseArt,uv), selected=texture2D(frameArt,uv);
        float weight=1.0;
        if(localEyes>0.5) weight=max(eyeWeight(artPoint,eyeA,eyeAngles.x),eyeWeight(artPoint,eyeB,eyeAngles.y));
        vec4 pixel=mix(original,selected,weight);
        if(pixel.a<0.02) discard;
        gl_FragColor=pixel;
      }`
    const shaders: WebGLShader[] = []
    try {
      const compile = (type: number, source: string) => {
        const shader = gl.createShader(type)
        if (!shader) throw new Error('无法分配桌宠着色器。')
        shaders.push(shader)
        gl.shaderSource(shader, source)
        gl.compileShader(shader)
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) || 'Shader error')
        return shader
      }
      const vs = compile(gl.VERTEX_SHADER, vertex), fs = compile(gl.FRAGMENT_SHADER, fragment)
      const program = gl.createProgram()
      if (!program) throw new Error('无法建立桌宠图形程序。')
      this.program = program
      gl.attachShader(program, vs)
      gl.attachShader(program, fs)
      gl.linkProgram(program)
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) || 'Link error')
      gl.useProgram(program)
      const vertices: number[] = [], columns = 24, rows = 48
      for (let row = 0; row < rows; row++) for (let column = 0; column < columns; column++) {
        const x0 = column * manifest.width / columns, x1 = (column + 1) * manifest.width / columns
        const y0 = row * manifest.height / rows, y1 = (row + 1) * manifest.height / rows
        vertices.push(x0, y0, x1, y0, x0, y1, x1, y0, x1, y1, x0, y1)
      }
      this.vertexCount = vertices.length / 2
      const buffer = gl.createBuffer()
      if (!buffer) throw new Error('无法分配桌宠网格。')
      this.buffer = buffer
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.STATIC_DRAW)
      const attribute = gl.getAttribLocation(program, 'position')
      gl.enableVertexAttribArray(attribute)
      gl.vertexAttribPointer(attribute, 2, gl.FLOAT, false, 0, 0)
      this.uniforms = Object.fromEntries(['size', 'movement', 'anchors', 'artRect', 'localEyes', 'eyeA', 'eyeB', 'eyeAngles']
        .map(id => [id, gl.getUniformLocation(program, id)]))
      gl.uniform2f(this.uniforms.size, manifest.width, manifest.height)
      gl.uniform1i(gl.getUniformLocation(program, 'baseArt'), 0)
      gl.uniform1i(gl.getUniformLocation(program, 'frameArt'), 1)
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true)
      for (const file of Object.keys(manifest.sha256)) {
        const image = images.get(file)
        if (!image) throw new Error(`Missing pose frame: ${file}`)
        const texture = gl.createTexture()
        if (!texture) throw new Error('无法分配桌宠纹理。')
        this.textures.set(file, texture)
        gl.bindTexture(gl.TEXTURE_2D, texture)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image)
      }
      gl.viewport(0, 0, manifest.width, manifest.height)
      gl.clearColor(0, 0, 0, 0)
    } catch (error) {
      gl.getExtension('WEBGL_lose_context')?.loseContext()
      throw error
    } finally {
      shaders.forEach(shader => gl.deleteShader(shader))
    }
  }

  draw(pose: Pose, motion: PoseMotion) {
    const gl = this.gl, texture = this.textures.get(pose.files[motion.frame])
    if (!texture) throw new Error(`Missing pose frame: ${pose.id}/${motion.frame}`)
    if (gl.isContextLost()) throw new Error('桌宠图形上下文已丢失，请重新打开桌宠。')
    gl.clear(gl.COLOR_BUFFER_BIT)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, this.textures.get(pose.files[0])!)
    gl.activeTexture(gl.TEXTURE1)
    gl.bindTexture(gl.TEXTURE_2D, texture)
    const [x, y, width, height] = poseArtRect(pose)
    gl.uniform4f(this.uniforms.artRect, x, y, width, height)
    gl.uniform1f(this.uniforms.localEyes, pose.frameMode === 'local_eyes' ? 1 : 0)
    if (pose.frameMode === 'local_eyes') {
      if (!pose.eyeRegions) throw new Error(`Missing eye masks: ${pose.id}`)
      const [a, b] = pose.eyeRegions
      gl.uniform4f(this.uniforms.eyeA, a[0], a[1], a[2], a[3])
      gl.uniform4f(this.uniforms.eyeB, b[0], b[1], b[2], b[3])
      gl.uniform2f(this.uniforms.eyeAngles, a[4] * Math.PI / 180, b[4] * Math.PI / 180)
    }
    gl.uniform3f(this.uniforms.movement, motion.lean, motion.nod, motion.breath)
    gl.uniform2f(this.uniforms.anchors, pose.neckY, pose.hipY)
    gl.drawArrays(gl.TRIANGLES, 0, this.vertexCount)
    return this.canvas
  }

  dispose() {
    if (this.disposed) return
    this.disposed = true
    for (const texture of this.textures.values()) this.gl.deleteTexture(texture)
    this.gl.deleteBuffer(this.buffer)
    this.gl.deleteProgram(this.program)
    this.gl.getExtension('WEBGL_lose_context')?.loseContext()
  }
}
