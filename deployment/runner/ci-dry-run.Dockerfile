# Controller protocol fixture only: no application dependencies or release image.
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN apk add --no-cache python3
ARG OWNER
LABEL org.ragflow.ci-dry-run=$OWNER
WORKDIR /ragflow
COPY image-source/ /ragflow/
ENTRYPOINT ["python3"]
CMD ["--version"]
