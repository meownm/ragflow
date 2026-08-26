import DocumentPreview from '@/components/document-preview';
import { FileIcon } from '@/components/icon-font';
import { Modal } from '@/components/ui/modal/modal';
import {
  useGetChunkHighlights,
  useGetDocumentUrl,
} from '@/hooks/use-document-request';
import { IModalProps } from '@/interfaces/common';
import { IReferenceChunk } from '@/interfaces/database/chat';
import { IChunk } from '@/interfaces/database/dataset';
import { cn } from '@/lib/utils';
import { useEffect, useState } from 'react';

interface IProps extends IModalProps<any> {
  documentId: string;
  chunk: IChunk | IReferenceChunk;
}
function getFileExtensionRegex(filename: string): string {
  const match = filename.match(/\.([^.]+)$/);
  return match ? match[1].toLowerCase() : '';
}
const PdfDrawer = ({
  visible = false,
  hideModal,
  documentId,
  chunk,
}: IProps) => {
  const documentName =
    'document_name' in chunk ? chunk.document_name : chunk.doc_name;
  const getDocumentUrl = useGetDocumentUrl(documentId);
  const { highlights, setWidthAndHeight } = useGetChunkHighlights(chunk);
  // const ref = useRef<(highlight: IHighlight) => void>(() => {});
  // const [loaded, setLoaded] = useState(false);
  const url = getDocumentUrl();

  const [fileType, setFileType] = useState('');

  useEffect(() => {
    if (documentName) {
      const type = getFileExtensionRegex(documentName);
      setFileType(type);
    }
  }, [documentName]);
  return (
    <Modal
      title={
        <div className="flex items-center gap-2">
          <FileIcon name={documentName}></FileIcon>
          {documentName}
        </div>
      }
      onCancel={hideModal}
      open={visible}
      showfooter={false}
    >
      <DocumentPreview
        className={cn(
          '!h-[calc(100dvh-300px)] overflow-auto border-none padding-0 max-h-full',
        )}
        fileType={fileType}
        highlights={highlights}
        setWidthAndHeight={setWidthAndHeight}
        url={url}
      ></DocumentPreview>
    </Modal>
  );
};

export default PdfDrawer;
