import { AudioButton } from '@/components/ui/audio-button';

export function appendVoiceTranscript(
  value: string,
  transcript: string,
  maxLength?: number,
) {
  const normalizedTranscript = transcript.trim();
  if (!normalizedTranscript) return value;

  const separator = value && !/\s$/.test(value) ? ' ' : '';
  const nextValue = `${value}${separator}${normalizedTranscript}`;
  return maxLength ? nextValue.slice(0, maxLength) : nextValue;
}

interface VoiceInputProps {
  label: string;
  disabled?: boolean;
  onTranscript: (transcript: string) => void;
  testId?: string;
}

export function VoiceInput({
  label,
  disabled,
  onTranscript,
  testId,
}: VoiceInputProps) {
  return (
    <AudioButton
      ariaLabel={`Голосовой ввод: ${label}`}
      disabled={disabled}
      onOk={onTranscript}
      testId={testId}
    />
  );
}
