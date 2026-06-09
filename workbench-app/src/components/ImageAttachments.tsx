import { useCallback, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, ClipboardEvent } from "react";

export interface ImageAttachment {
  id: string;
  file: File;
  preview: string;
  base64: string;
  mimeType: string;
}

interface ImageAttachmentsProps {
  images: ImageAttachment[];
  onImagesChange: (images: ImageAttachment[]) => void;
  disabled?: boolean;
  maxImages?: number;
  maxSizeMB?: number;
}

const DEFAULT_MAX_IMAGES = 5;
const DEFAULT_MAX_SIZE_MB = 10;
const SUPPORTED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

/**
 * 图片附件组件
 *
 * 功能：
 * 1. 点击按钮选择图片
 * 2. 粘贴图片（Ctrl+V）
 * 3. 拖拽图片到区域
 * 4. 预览缩略图（点击放大查看）
 * 5. 删除单张图片
 * 6. 图片大小和数量限制
 */
export function ImageAttachments({
  images,
  onImagesChange,
  disabled = false,
  maxImages = DEFAULT_MAX_IMAGES,
  maxSizeMB = DEFAULT_MAX_SIZE_MB
}: ImageAttachmentsProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [previewImage, setPreviewImage] = useState<ImageAttachment | null>(null);

  const processFiles = useCallback(
    async (files: File[]) => {
      if (disabled) {
        return;
      }

      const validFiles = files.filter((file) => {
        // 检查文件类型
        if (!SUPPORTED_IMAGE_TYPES.includes(file.type)) {
          alert(`不支持的图片格式: ${file.name}`);
          return false;
        }

        // 检查文件大小
        const sizeMB = file.size / (1024 * 1024);
        if (sizeMB > maxSizeMB) {
          alert(`图片 ${file.name} 超过大小限制 ${maxSizeMB}MB`);
          return false;
        }

        return true;
      });

      // 检查数量限制
      if (images.length + validFiles.length > maxImages) {
        alert(`最多只能上传 ${maxImages} 张图片`);
        return;
      }

      // 转换为 ImageAttachment
      const newAttachments = await Promise.all(
        validFiles.map(
          (file): Promise<ImageAttachment> =>
            new Promise((resolve, reject) => {
              const reader = new FileReader();

              reader.onload = () => {
                const result = reader.result as string;
                // result 格式: data:image/png;base64,iVBORw0KG...
                const base64Data = result.split(",")[1];

                resolve({
                  id: `${Date.now()}-${Math.random()}`,
                  file,
                  preview: result,
                  base64: base64Data,
                  mimeType: file.type
                });
              };

              reader.onerror = () => reject(new Error(`Failed to read ${file.name}`));
              reader.readAsDataURL(file);
            })
        )
      );

      onImagesChange([...images, ...newAttachments]);
    },
    [images, onImagesChange, disabled, maxImages, maxSizeMB]
  );

  const handleFileSelect = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files || []);
      processFiles(files);
      // 重置 input 以便选择相同文件
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    },
    [processFiles]
  );

  const handlePaste = useCallback(
    (event: ClipboardEvent<HTMLDivElement>) => {
      const items = Array.from(event.clipboardData.items);
      const imageFiles = items
        .filter((item) => item.type.startsWith("image/"))
        .map((item) => item.getAsFile())
        .filter((file): file is File => file !== null);

      if (imageFiles.length > 0) {
        event.preventDefault();
        processFiles(imageFiles);
      }
    },
    [processFiles]
  );

  const handleDragEnter = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(true);
  }, []);

  const handleDragLeave = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(false);
  }, []);

  const handleDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.stopPropagation();
      setDragActive(false);

      const files = Array.from(event.dataTransfer.files);
      processFiles(files);
    },
    [processFiles]
  );

  const handleRemoveImage = useCallback(
    (id: string) => {
      onImagesChange(images.filter((img) => img.id !== id));
    },
    [images, onImagesChange]
  );

  const handleClickAttach = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  return (
    <div
      className="image-attachments"
      onPaste={handlePaste}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {images.length > 0 && (
        <div className="image-attachments__previews">
          {images.map((image) => (
            <div key={image.id} className="image-attachment-preview">
              <img
                src={image.preview}
                alt={image.file.name}
                className="image-attachment-preview__thumbnail"
                onClick={() => setPreviewImage(image)}
                title="点击查看大图"
              />
              <button
                type="button"
                className="image-attachment-preview__remove"
                onClick={() => handleRemoveImage(image.id)}
                disabled={disabled}
                aria-label="删除图片"
              >
                ×
              </button>
              <div className="image-attachment-preview__name" title={image.file.name}>
                {image.file.name}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className={`image-attachments__dropzone ${dragActive ? "image-attachments__dropzone--active" : ""}`}>
        <input
          ref={fileInputRef}
          type="file"
          accept={SUPPORTED_IMAGE_TYPES.join(",")}
          multiple
          onChange={handleFileSelect}
          disabled={disabled}
          style={{ display: "none" }}
        />
        <button
          type="button"
          className="image-attachments__attach-button"
          onClick={handleClickAttach}
          disabled={disabled || images.length >= maxImages}
          title="添加图片（支持点击、粘贴、拖拽）"
        >
          📎 {images.length > 0 ? `已选 ${images.length}/${maxImages}` : "添加图片"}
        </button>
      </div>

      {/* 图片预览模态框 */}
      {previewImage !== null && (
        <div className="image-preview-modal" onClick={() => setPreviewImage(null)}>
          <div className="image-preview-modal__content" onClick={(e) => e.stopPropagation()}>
            <img
              src={previewImage.preview}
              alt={previewImage.file.name}
              className="image-preview-modal__image"
            />
            <div className="image-preview-modal__info">
              <span>{previewImage.file.name}</span>
              <span>{(previewImage.file.size / 1024).toFixed(1)} KB</span>
            </div>
            <button
              type="button"
              className="image-preview-modal__close"
              onClick={() => setPreviewImage(null)}
            >
              关闭
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
