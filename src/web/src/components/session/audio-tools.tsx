import type { Run, Session } from "../../lib/types";

const AUDIO_ACCEPT = ".wav,.mp3";
const AUDIO_MAX_SIZE_MB = 25;

type Props = { session: Session | null; file: File | null; setFile: (file: File | null) => void; manualTranscript: string; setManualTranscript: (value: string) => void; status: string; activeRun: Run | null; readOnly: boolean; onUpload: () => void; onSaveTranscript: () => void };

export function AudioTools(props: Props) {
  const disabled = Boolean(props.activeRun) || props.readOnly || !props.session;
  return <div className="shrink-0 border-t border-slate-100 bg-slate-50 px-4 py-3 sm:px-8"><div className="mx-auto max-w-3xl"><div className="flex flex-wrap items-center gap-2 text-xs"><label className={`border border-slate-300 bg-white px-3 py-1.5 ${disabled ? "cursor-not-allowed text-slate-400" : "cursor-pointer text-slate-700"}`}><input aria-label="选择音频文件" type="file" accept={AUDIO_ACCEPT} className="hidden" disabled={disabled} onChange={(event) => props.setFile(event.target.files?.[0] || null)} />选择音频</label>{props.file && <span className="max-w-[220px] truncate text-slate-600">{props.file.name}</span>}{props.file && <button type="button" onClick={props.onUpload} disabled={disabled} className="border border-primary-300 px-3 py-1.5 text-primary-700">上传并转写</button>}<span className="text-slate-500">{props.status || (props.session ? `支持 ${AUDIO_ACCEPT}，最大 ${AUDIO_MAX_SIZE_MB}MB` : "进入会话后可上传音频")}</span></div><div className="mt-2 flex gap-2"><textarea aria-label="手动转写文本" value={props.manualTranscript} onChange={(event) => props.setManualTranscript(event.target.value)} disabled={Boolean(props.activeRun) || props.readOnly} rows={1} placeholder="无法转写时粘贴文本" className="min-w-0 flex-1 border border-slate-300 bg-white p-2 text-xs outline-none focus:border-primary-500" /><button type="button" onClick={props.onSaveTranscript} disabled={Boolean(props.activeRun) || props.readOnly || !props.manualTranscript.trim()} className="border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700">填入诊断</button></div></div></div>;
}
