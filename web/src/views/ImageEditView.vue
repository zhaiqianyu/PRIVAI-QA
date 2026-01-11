<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { message } from 'ant-design-vue'
import { UploadOutlined, DownloadOutlined } from '@ant-design/icons-vue'

import HeaderComponent from '@/components/HeaderComponent.vue'
import { dashboardApi } from '@/apis/dashboard_api'

const prompt = ref('')
const selectedFile = ref(null)
const isSubmitting = ref(false)

const sourcePreviewUrl = ref('')
const resultPreviewUrl = ref('')

const canSubmit = computed(() => Boolean(selectedFile.value) && Boolean(prompt.value?.trim()) && !isSubmitting.value)

const revokeObjectUrl = (url) => {
  if (url) URL.revokeObjectURL(url)
}

onBeforeUnmount(() => {
  revokeObjectUrl(sourcePreviewUrl.value)
  revokeObjectUrl(resultPreviewUrl.value)
})

const handleFileChange = (file) => {
  revokeObjectUrl(sourcePreviewUrl.value)
  revokeObjectUrl(resultPreviewUrl.value)

  selectedFile.value = file
  sourcePreviewUrl.value = URL.createObjectURL(file)
  resultPreviewUrl.value = ''
}

const handleUploadChange = (info) => {
  const file = info?.file?.originFileObj || info?.file
  if (!file) return
  if (file?.type && !String(file.type).startsWith('image/')) {
    message.error('请选择图片文件')
    return
  }
  handleFileChange(file)
  message.success('上传成功')
}

const submitEdit = async () => {
  if (!selectedFile.value) {
    message.warning('请先选择图片')
    return
  }
  if (!prompt.value?.trim()) {
    message.warning('请输入修图要求')
    return
  }

  try {
    isSubmitting.value = true
    const response = await dashboardApi.editImage({ image: selectedFile.value, prompt: prompt.value.trim() })
    const blob = await response.blob()

    revokeObjectUrl(resultPreviewUrl.value)
    resultPreviewUrl.value = URL.createObjectURL(blob)
    message.success('修图完成')
  } catch (e) {
    message.error(e?.message || '修图失败')
  } finally {
    isSubmitting.value = false
  }
}

const downloadResult = () => {
  if (!resultPreviewUrl.value) return
  const a = document.createElement('a')
  a.href = resultPreviewUrl.value
  a.download = 'edited.png'
  a.click()
}
</script>

<template>
  <div class="layout-container image-edit-container">
    <HeaderComponent title="修图" description="输入修图要求，生成结果图
    ">
      <template #actions>
        <a-button
          type="primary"
          :loading="isSubmitting"
          :disabled="!canSubmit"
          @click="submitEdit"
        >
          开始修图
        </a-button>
      </template>
    </HeaderComponent>

    <div class="content">
      <a-row :gutter="16">
        <a-col :xs="24" :lg="12">
          <a-card title="输入" class="panel">
            <a-space direction="vertical" style="width: 100%">
              <a-upload
                :show-upload-list="false"
                accept="image/*"
                :before-upload="() => false"
                :max-count="1"
                @change="handleUploadChange"
              >
                <a-button>
                  <template #icon><UploadOutlined /></template>
                  选择图片
                </a-button>
              </a-upload>

              <div v-if="sourcePreviewUrl" class="preview">
                <img :src="sourcePreviewUrl" alt="source" />
              </div>

              <a-textarea
                v-model:value="prompt"
                :rows="3"
                placeholder="例如：去掉背景杂物、整体提亮、增强清晰度，保持人物不变"
                allow-clear
              />
            </a-space>
          </a-card>
        </a-col>

        <a-col :xs="24" :lg="12">
          <a-card title="结果" class="panel">
            <a-space direction="vertical" style="width: 100%">
              <a-button
                :disabled="!resultPreviewUrl"
                @click="downloadResult"
              >
                <template #icon><DownloadOutlined /></template>
                下载图片
              </a-button>

              <div v-if="resultPreviewUrl" class="preview">
                <img :src="resultPreviewUrl" alt="result" />
              </div>
              <a-empty v-else description="暂无结果，请先选择图片并输入修图要求" />
            </a-space>
          </a-card>
        </a-col>
      </a-row>
    </div>
  </div>
</template>

<style lang="less" scoped>
.image-edit-container {
  background: var(--color-bg-container);
}

.content {
  padding: 16px;
}

.panel {
  border-radius: 8px;
}

.preview {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  padding: 8px;
  background: var(--gray-10);
  height: clamp(240px, 60vh, 720px);
  display: flex;
  align-items: center;
  justify-content: center;

  img {
    width: 100%;
    height: 100%;
    display: block;
    border-radius: 6px;
    object-fit: contain;
  }
}
</style>
