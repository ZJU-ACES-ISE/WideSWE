### Description

Specifying a label `mylabel=foo#bar` results in the label `mylabel=foo`. It appears that the octothorpe (#) is incorrectly interpreted as the start of a comment and the rest of the label is removed.

### Expected behaviour

The Docker labels output of the action should include `mylabel=foo#bar`

### Actual behaviour

The Docker labels output of the action includes `mylabel=foo`

### YAML workflow

```yaml
-
        name: Docker meta
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          labels: |
            mylabel=foo#bar
```

### Additional info

The behaviour was correct in v3.6.2 and seems to have broken around v3.7.0.

Full-line `#` comments in multiline inputs should remain supported, while `#` characters inside actual values must be preserved. This behavior should be consistent across the list-style inputs: images, tags, flavor, labels, and annotations.
